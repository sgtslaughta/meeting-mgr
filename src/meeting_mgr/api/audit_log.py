from fastapi import APIRouter, Depends

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import require_role
from meeting_mgr.db import get_readonly_org_session
from meeting_mgr.models import Account, AuditLogEntry

router = APIRouter()


@router.get("/audit-log")
def read_audit_log(
    account: Account = Depends(get_current_account),
    limit: int = 50,
    offset: int = 0,
):
    require_role(account, frozenset({"admin", "auditor"}))
    with get_readonly_org_session(account.organization_id) as s:
        rows = (
            s.query(AuditLogEntry)
            .order_by(AuditLogEntry.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": e.id,
                "actor_account_id": e.actor_account_id,
                "action": e.action,
                "target": e.target,
                "detail": e.detail,
                "created_at": e.created_at,
            }
            for e in rows
        ]
