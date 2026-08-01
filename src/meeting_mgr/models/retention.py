from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class RetentionPolicy(Base):
    """One row per Organization. Both day counts are optional and independent
    -- see design spec §7: keep-forever is the default, audio-only purge and
    full-Meeting purge are each opt-in."""

    __tablename__ = "retention_policy"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), unique=True)
    audio_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meeting_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
