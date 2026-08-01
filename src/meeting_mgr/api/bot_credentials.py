from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from meeting_mgr.audit import record_audit
from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import require_role
from meeting_mgr.bot_credentials import (
    create_bot_credential,
    list_bot_credentials,
    revoke_bot_credential,
)
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account

router = APIRouter(prefix="/bot-credentials")

_ADMIN_ONLY = frozenset({"admin"})


class BotCredentialIn(BaseModel):
    label: str
    owner_account_id: int


def _view(cred) -> dict:
    return {
        "id": cred.id,
        "label": cred.label,
        "owner_account_id": cred.owner_account_id,
        "revoked_at": cred.revoked_at,
        "created_at": cred.created_at,
    }


@router.get("")
def read_bot_credentials(account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_readonly_org_session(account.organization_id) as s:
        return [_view(c) for c in list_bot_credentials(s, account.organization_id)]


@router.post("", status_code=201)
def write_bot_credential(body: BotCredentialIn, account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_org_session(account.organization_id) as s:
        owner = s.get(Account, body.owner_account_id)
        if owner is None or owner.organization_id != account.organization_id:
            raise HTTPException(422, "owner_account_id must belong to your organization")
        cred, token = create_bot_credential(
            s, account.organization_id, label=body.label, owner_account_id=body.owner_account_id
        )
        record_audit(
            s,
            organization_id=account.organization_id,
            actor_account_id=account.id,
            action="bot_credential.create",
            target=f"bot_credential:{cred.id}",
            detail={"label": body.label, "owner_account_id": body.owner_account_id},
        )
        view = _view(cred)
    view["token"] = token
    return view


@router.post("/{credential_id}/revoke")
def revoke(credential_id: int, account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_org_session(account.organization_id) as s:
        cred = revoke_bot_credential(s, account.organization_id, credential_id)
        if cred is None:
            raise HTTPException(404, "bot credential not found")
        record_audit(
            s,
            organization_id=account.organization_id,
            actor_account_id=account.id,
            action="bot_credential.revoke",
            target=f"bot_credential:{cred.id}",
        )
        view = _view(cred)
    return view
