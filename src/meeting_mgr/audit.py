"""The single writer of AuditLogEntry rows.

Mirrors provenance.confirm(): one function, one place to audit, no update or
delete counterpart anywhere in the codebase.
"""

from sqlalchemy.orm import Session as _Session

from meeting_mgr.models import AuditLogEntry as _AuditLogEntry


def record_audit(
    s: _Session,
    *,
    organization_id: int,
    actor_account_id: int | None,
    action: str,
    target: str,
    detail: dict | None = None,
) -> _AuditLogEntry:
    entry = _AuditLogEntry(
        organization_id=organization_id,
        actor_account_id=actor_account_id,
        action=action,
        target=target,
        detail=detail or {},
    )
    s.add(entry)
    return entry
