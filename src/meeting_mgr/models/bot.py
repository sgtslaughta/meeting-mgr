from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class BotCredential(Base):
    """Identity for a bot process, which has no session and no browser --
    the meeting-bot analogue of WatchFolder (Phase 5). organization_id and
    owner_account_id are set once, explicitly, by an admin (api/bot_credentials.py)
    and never inferred from the token. token_hash is a salted PBKDF2 hash
    (meeting_mgr.auth.password.hash_password) of the bearer secret; the
    plaintext secret is never stored and is returned to the admin exactly
    once, at creation.

    label is unique per organization (uq_bot_credential_org_label, migration
    b8c2e6f0a1d3), matching WatchFolder's uq_watch_folder_org_path. This is
    cosmetic/UX only: routing keys off organization_id and
    bot_credential_id, never the label."""

    __tablename__ = "bot_credential"
    __table_args__ = (
        UniqueConstraint("organization_id", "label", name="uq_bot_credential_org_label"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    owner_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    label: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(200))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BotSession(Base):
    """One bot-initiated recording session -- created in Task 5, defined here
    alongside BotCredential so models/__init__.py has a single import site
    for both. See Task 5 for field-by-field rationale.

    TENANCY IS NOT ENFORCED BY THE SCHEMA. organization_id is the column the
    RLS policy filters on, but nothing checks it agrees with the organization
    of bot_credential_id or meeting_id -- a CHECK constraint cannot span
    tables. This was verified live: as meeting_app with app.org_id set to one
    org, a row claiming that organization_id while both FKs pointed at another
    org's rows was accepted.

    The invariant holds only because api/bot.py derives organization_id from
    the authenticated credential and creates the Meeting itself, so no caller
    ever supplies either FK id. Any future code path that accepts a
    caller-supplied meeting_id or bot_credential_id must re-establish it in
    application code. Same gap exists on BotCredential and WatchFolder -- it
    is the repo's organization_id-direct pattern, not an oversight here."""

    __tablename__ = "bot_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    bot_credential_id: Mapped[int] = mapped_column(ForeignKey("bot_credential.id"))
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meeting.id", ondelete="CASCADE"), unique=True
    )
    platform_meeting_id: Mapped[str] = mapped_column(String(300))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
