from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class BotCredential(Base):
    """Identity for a bot process, which has no session and no browser --
    the meeting-bot analogue of WatchFolder (Phase 5). organization_id and
    owner_account_id are set once, explicitly, by an admin (api/bot_credentials.py)
    and never inferred from the token. token_hash is a salted PBKDF2 hash
    (meeting_mgr.auth.password.hash_password) of the bearer secret; the
    plaintext secret is never stored and is returned to the admin exactly
    once, at creation."""

    __tablename__ = "bot_credential"

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
    for both. See Task 5 for field-by-field rationale."""

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
