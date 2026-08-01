from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class AuditLogEntry(Base):
    """Append-only. No Role can edit or delete an entry — see
    src/meeting_mgr/audit.py, the only writer."""

    __tablename__ = "audit_log_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    actor_account_id: Mapped[int | None] = mapped_column(ForeignKey("account.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
