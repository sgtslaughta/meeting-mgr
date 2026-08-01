from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base

ROLES = ("admin", "member", "auditor")


class Account(Base):
    """Someone who logs into Meeting-MGR. Never a Participant — most
    Participants never have an Account, and an Account is not "found in" a
    recording the way a Participant is."""

    __tablename__ = "account"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_account_org_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20), default="member")
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    mtls_subject: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
