from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from meeting_mgr.auth.bot_deps import get_bot_credential
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import BotCredential, BotSession, Meeting
from meeting_mgr.storage import ensure_bucket, list_keys, put_stream

router = APIRouter(prefix="/bot")


class StartSessionIn(BaseModel):
    platform_meeting_id: str
    title: str


def _response(session: BotSession, meeting: Meeting, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"session_id": session.id, "meeting_id": meeting.id, "status": meeting.status},
    )


@router.post("/sessions", status_code=201)
def start_session(body: StartSessionIn, credential: BotCredential = Depends(get_bot_credential)):
    # No require_role()/authorize(): a bot is not an Account and no Meeting
    # exists yet to authorize against. The credential itself -- valid,
    # unrevoked, resolved by get_bot_credential -- is the authorization: it
    # may only ever act as its own organization_id, which is the sole source
    # of tenancy here (never client input).
    with get_org_session(credential.organization_id) as s:
        existing = (
            s.query(BotSession)
            .filter_by(
                bot_credential_id=credential.id, platform_meeting_id=body.platform_meeting_id
            )
            .one_or_none()
        )
        if existing is not None:
            m = s.get(Meeting, existing.meeting_id)
            return _response(existing, m, status_code=200)

        m = Meeting(
            organization_id=credential.organization_id,
            owner_account_id=credential.owner_account_id,
            title=body.title,
            status="capturing",
        )
        s.add(m)
        s.flush()
        session = BotSession(
            organization_id=credential.organization_id,
            bot_credential_id=credential.id,
            meeting_id=m.id,
            platform_meeting_id=body.platform_meeting_id,
        )
        s.add(session)
        s.flush()
        return _response(session, m, status_code=201)


def _bot_chunk_prefix(meeting_id: int) -> str:
    return f"raw/{meeting_id}/bot-chunks/"


def _bot_chunk_key(meeting_id: int, seq: int) -> str:
    return f"{_bot_chunk_prefix(meeting_id)}{seq:06d}.chunk"


def _bot_chunk_seq(prefix: str, key: str) -> int:
    return int(key.removeprefix(prefix).removesuffix(".chunk"))


def _owned_session(s, session_id: int, credential: BotCredential) -> BotSession:
    """Look up a session and verify it belongs to the authenticated
    credential -- 404, not 403, on any mismatch (wrong credential or wrong
    organization), same reasoning as authz.authorize()'s tenant-mismatch
    branch: a distinguishable status code would let one bot credential
    enumerate another's session ids."""
    session = (
        s.query(BotSession)
        .filter_by(
            id=session_id,
            bot_credential_id=credential.id,
            organization_id=credential.organization_id,
        )
        .one_or_none()
    )
    if session is None:
        raise HTTPException(404, "bot session not found")
    return session


@router.put("/sessions/{session_id}/chunks/{seq}")
def upload_chunk(
    session_id: int,
    seq: int,
    chunk: UploadFile = File(...),
    credential: BotCredential = Depends(get_bot_credential),
):
    with get_org_session(credential.organization_id) as s:
        session = _owned_session(s, session_id, credential)
        m = s.get(Meeting, session.meeting_id)
        if m.status != "capturing":
            raise HTTPException(409, "session is not accepting chunks")
        meeting_id = m.id
        ensure_bucket()
        put_stream(_bot_chunk_key(meeting_id, seq), chunk.file)
        session.last_activity_at = datetime.utcnow()
    return {"seq": seq}


@router.get("/sessions/{session_id}/chunks")
def list_chunks(session_id: int, credential: BotCredential = Depends(get_bot_credential)):
    with get_readonly_org_session(credential.organization_id) as s:
        session = _owned_session(s, session_id, credential)
        meeting_id = session.meeting_id
    prefix = _bot_chunk_prefix(meeting_id)
    seqs = sorted(_bot_chunk_seq(prefix, k) for k in list_keys(prefix))
    return {"seqs": seqs}
