from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

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
        # Pass 1 established (VERIFIED): under get_org_session, tenant_isolation
        # RLS on `account` already makes s.get() return None for any
        # cross-org id, so `owner is None` alone accounts for every 422 this
        # raises today -- `owner.organization_id != account.organization_id`
        # is currently unreachable dead code. See
        # test_cross_org_owner_lookup_returns_none_under_rls_is_the_active_guard
        # and test_owner_organization_mismatch_comparison_is_correct_if_ever_reached
        # in test_api_bot_credentials.py, which isolate the two halves.
        # Kept deliberately anyway: it is the only thing that would still
        # catch a cross-org owner if this endpoint were ever moved off
        # get_org_session onto a non-RLS session (get_session()). Do not
        # delete a guard because it is unreachable today -- that is how the
        # reachable case comes back later unnoticed.
        if owner is None or owner.organization_id != account.organization_id:
            raise HTTPException(422, "owner_account_id must belong to your organization")
        # uq_bot_credential_org_label makes a repeat label an IntegrityError;
        # without this it surfaced as an uncaught 500. No SAVEPOINT needed:
        # this path raises immediately and never touches the session again,
        # so the aborted transaction is rolled back by the context manager
        # rather than committed. Anything added AFTER this except -- an audit
        # of the failed attempt, say -- would need begin_nested(), since a
        # further query on an aborted transaction raises PendingRollbackError.
        # (Verified: wrapping this in begin_nested() changes no test outcome.)
        try:
            cred, token = create_bot_credential(
                s, account.organization_id, label=body.label, owner_account_id=body.owner_account_id
            )
            s.flush()
        except IntegrityError:
            raise HTTPException(409, "a bot credential with that label already exists") from None
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
