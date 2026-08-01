import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import authorize, require_role
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account, Meeting, Recording
from meeting_mgr.storage import ensure_bucket, list_keys, put_object, put_stream

router = APIRouter()

_CAN_CAPTURE = frozenset({"admin", "member"})


def _chunk_prefix(meeting_id: int) -> str:
    return f"raw/{meeting_id}/chunks/"


def _chunk_key(meeting_id: int, seq: int) -> str:
    return f"{_chunk_prefix(meeting_id)}{seq:06d}.webm"


def _chunk_seq(prefix: str, key: str) -> int:
    return int(key.removeprefix(prefix).removesuffix(".webm"))


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


@router.post("/meetings/{meeting_id}/capture/finish")
def finish_capture(meeting_id: int, account: Account = Depends(get_current_account)):
    with get_org_session(account.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        authorize(account, m, s, write=True)
        if m.status != "capturing":
            raise HTTPException(409, "meeting is not in capture state")
        prefix = _chunk_prefix(meeting_id)
        # Ordered numerically by sequence number, not by the lexicographic
        # order list_keys() returns. Chunk keys happen to be zero-padded to
        # 6 digits (_chunk_key), so string order matches numeric order today
        # -- but sorting on the parsed seq is what actually guarantees it,
        # rather than depending on that formatting choice staying fixed.
        keys = sorted(list_keys(prefix), key=lambda k: _chunk_seq(prefix, k))
        if not keys:
            # An empty capture (finish with zero chunks uploaded) is rejected
            # outright rather than enqueuing a pipeline run that would fail
            # confusingly downstream trying to normalize an empty manifest.
            raise HTTPException(422, "no chunks were uploaded")
        # A gap in the sequence (e.g. chunks 1, 2, 4 exist) is NOT treated as
        # an error here: the client-supplied seq gives no way to distinguish
        # "chunk 3 was dropped" from "chunk 3 was never recorded" (silence
        # detection, a paused capture, etc). This manifest is built on client
        # discipline, not a content-addressed/counted guarantee (see
        # upload_chunk's docstring), so a gap is accepted as a lossy capture:
        # whatever chunks exist, in order, go in the manifest.
        manifest_key = f"raw/{meeting_id}/manifest.json"
        put_object(manifest_key, json.dumps(keys).encode())
        s.add(Recording(meeting_id=meeting_id, raw_key=f"manifest:{manifest_key}"))
        m.status = "pending"
    run_pipeline(meeting_id)
    return {"meeting_id": meeting_id, "status": "pending"}
