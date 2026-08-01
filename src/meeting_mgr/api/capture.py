import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import authorize, require_role
from meeting_mgr.chunk_storage import chunk_key, chunk_prefix, chunk_seq
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account, Meeting, Recording
from meeting_mgr.storage import ensure_bucket, list_keys, put_object, put_stream

router = APIRouter()

_CAN_CAPTURE = frozenset({"admin", "member"})
_SUBDIR, _SUFFIX = "chunks", ".webm"


def _chunk_prefix(meeting_id: int) -> str:
    return chunk_prefix(meeting_id, _SUBDIR)


def _chunk_key(meeting_id: int, seq: int) -> str:
    return chunk_key(meeting_id, seq, _SUBDIR, _SUFFIX)


def _chunk_seq(prefix: str, key: str) -> int:
    return chunk_seq(prefix, _SUFFIX, key)


def run_pipeline(meeting_id: int) -> None:
    """Module-level indirection on purpose (mirrors api/meetings.py): the
    import is deferred to call time, and tests monkeypatch
    meeting_mgr.api.capture.run_pipeline to spy on the dispatch mechanism
    itself rather than relying on task_always_eager's inline execution."""
    from meeting_mgr.pipeline.orchestrate import run_pipeline as task

    task.delay(meeting_id)


@router.post("/meetings/capture", status_code=201)
def start_capture(title: str = Form(...), account: Account = Depends(get_current_account)):
    # No Meeting exists yet -- require_role(), not authorize(), same reason
    # create_meeting() in api/meetings.py uses it: this is the exact shape
    # of ingest defect Phase 3 fixed (an auditor reaching ingest because
    # authorize() needs an existing Meeting to check).
    require_role(account, _CAN_CAPTURE)
    with get_org_session(account.organization_id) as s:
        m = Meeting(
            organization_id=account.organization_id,
            owner_account_id=account.id,
            title=title,
            status="capturing",
        )
        s.add(m)
        s.flush()
        meeting_id = m.id
    return {"meeting_id": meeting_id, "status": "capturing"}


@router.put("/meetings/{meeting_id}/capture/chunks/{seq}")
def upload_chunk(
    meeting_id: int,
    seq: int,
    chunk: UploadFile = File(...),
    account: Account = Depends(get_current_account),
):
    with get_org_session(account.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        authorize(account, m, s, write=True)
        if m.status != "capturing":
            raise HTTPException(409, "meeting is not accepting capture chunks")
    ensure_bucket()
    # No chunk-size assertion here: Content-Length is client-supplied
    # (not a real guarantee) and counting bytes ourselves would mean
    # buffering the chunk, which the "never buffer whole media in memory"
    # constraint rules out. normalize.py's _write_manifest_chunks (Task 11)
    # documents the multipart-threshold assumption this relies on instead.
    #
    # A retried seq (same number uploaded twice, e.g. a browser retrying
    # after a network blip) overwrites the previous object at the same key
    # -- S3 PutObject semantics -- rather than erroring or duplicating.
    # That is the right choice: it makes the retry idempotent instead of
    # forcing the client to detect "did my last attempt actually land"
    # before deciding whether to resend.
    put_stream(_chunk_key(meeting_id, seq), chunk.file)
    return {"seq": seq}


@router.get("/meetings/{meeting_id}/capture/chunks")
def list_chunks(meeting_id: int, account: Account = Depends(get_current_account)):
    with get_readonly_org_session(account.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        authorize(account, m, s)
    prefix = _chunk_prefix(meeting_id)
    seqs = sorted(int(k.removeprefix(prefix).removesuffix(".webm")) for k in list_keys(prefix))
    return {"seqs": seqs}


def _finish_race_hook(meeting_id: int) -> None:
    """No-op in production. Runs after finish_capture's status flip commits
    but before it takes the list_keys() snapshot -- tests monkeypatch this
    to simulate a straggler chunk upload landing in that window. See
    test_finish_toctou_* in test_api_capture.py."""


@router.post("/meetings/{meeting_id}/capture/finish")
def finish_capture(meeting_id: int, account: Account = Depends(get_current_account)):
    with get_org_session(account.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        authorize(account, m, s, write=True)
        if m.status != "capturing":
            raise HTTPException(409, "meeting is not in capture state")
        # TOCTOU fix: flip status FIRST, committed in this transaction alone,
        # before taking the list_keys() snapshot below. Previously the
        # status write was the LAST thing this function did (after building
        # and uploading the manifest), so a concurrent upload_chunk's own
        # status recheck kept reading "capturing" -- and was allowed to
        # put_stream() straight to storage -- for the entire duration this
        # function spent snapshotting and building the manifest. Committing
        # the flip here, first, means any upload_chunk whose OWN status
        # recheck begins after this commit sees "finishing" (not
        # "capturing") and is rejected with 409 before it ever writes to S3.
        # That is the ordering guarantee this closes.
        #
        # Residual window (NOT closed, stated honestly): an upload_chunk
        # request whose status recheck already committed and read
        # "capturing" a moment before this commit lands is already past its
        # check -- it is free to complete its put_stream() (a plain S3 call
        # with no DB transaction around it, see upload_chunk) at any point
        # afterward, including after the list_keys() snapshot below. That
        # chunk is still written to storage but silently excluded from the
        # manifest. This narrows the vulnerable window from "the entire body
        # of finish_capture" down to "one already-in-flight straggler's
        # remaining put_stream() latency" -- it does not eliminate it.
        m.status = "finishing"
    _finish_race_hook(meeting_id)
    prefix = _chunk_prefix(meeting_id)
    # Ordered numerically by sequence number, not by the lexicographic
    # order list_keys() returns. Chunk keys happen to be zero-padded to
    # 6 digits (_chunk_key), so string order matches numeric order today
    # -- but sorting on the parsed seq is what actually guarantees it,
    # rather than depending on that formatting choice staying fixed.
    keys = sorted(list_keys(prefix), key=lambda k: _chunk_seq(prefix, k))

    empty = False
    with get_org_session(account.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        if not keys:
            # An empty capture (finish with zero chunks uploaded, or none
            # landed even after the status flip above) is rejected outright
            # rather than enqueuing a pipeline run that would fail
            # confusingly downstream trying to normalize an empty manifest.
            # Revert to "capturing" so the client can retry (upload a chunk
            # it was still holding, then call finish again) -- same
            # recoverable shape the pre-fix 422 behavior had.
            m.status = "capturing"
            empty = True
        else:
            # A gap in the sequence (e.g. chunks 1, 2, 4 exist) is NOT
            # treated as an error here: the client-supplied seq gives no way
            # to distinguish "chunk 3 was dropped" from "chunk 3 was never
            # recorded" (silence detection, a paused capture, etc). This
            # manifest is built on client discipline, not a
            # content-addressed/counted guarantee (see upload_chunk's
            # docstring), so a gap is accepted as a lossy capture: whatever
            # chunks exist, in order, go in the manifest.
            manifest_key = f"raw/{meeting_id}/manifest.json"
            put_object(manifest_key, json.dumps(keys).encode())
            s.add(Recording(meeting_id=meeting_id, raw_key=f"manifest:{manifest_key}"))
            m.status = "pending"
    if empty:
        raise HTTPException(422, "no chunks were uploaded")
    run_pipeline(meeting_id)
    return {"meeting_id": meeting_id, "status": "pending"}
