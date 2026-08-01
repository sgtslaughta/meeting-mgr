from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import authorize, require_role
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account, Meeting
from meeting_mgr.storage import ensure_bucket, list_keys, put_stream

router = APIRouter()

_CAN_CAPTURE = frozenset({"admin", "member"})


def _chunk_prefix(meeting_id: int) -> str:
    return f"raw/{meeting_id}/chunks/"


def _chunk_key(meeting_id: int, seq: int) -> str:
    return f"{_chunk_prefix(meeting_id)}{seq:06d}.webm"


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
