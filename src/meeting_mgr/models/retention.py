from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class RetentionPolicy(Base):
    """One row per Organization. Both day counts are optional and independent
    -- see design spec §7: keep-forever is the default, audio-only purge and
    full-Meeting purge are each opt-in.

    Negative values are rejected at the DB level: NULL (keep forever) and 0
    (purge immediately) are both meaningful, but a negative value would put
    the purge cutoff (now - retention_days) in the future, making every
    Meeting in the organization eligible for deletion."""

    __tablename__ = "retention_policy"
    __table_args__ = (
        CheckConstraint(
            "audio_retention_days IS NULL OR audio_retention_days >= 0",
            name="ck_retention_policy_audio_retention_days_nonneg",
        ),
        CheckConstraint(
            "meeting_retention_days IS NULL OR meeting_retention_days >= 0",
            name="ck_retention_policy_meeting_retention_days_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"), unique=True)
    audio_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meeting_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
