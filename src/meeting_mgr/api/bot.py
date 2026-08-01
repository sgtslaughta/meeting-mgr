from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from meeting_mgr.auth.bot_deps import get_bot_credential
from meeting_mgr.db import get_org_session
from meeting_mgr.models import BotCredential, BotSession, Meeting

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
