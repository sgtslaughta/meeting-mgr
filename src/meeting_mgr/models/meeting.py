from datetime import datetime
from sqlalchemy import ForeignKey, Float, String, DateTime, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from meeting_mgr.db import Base

class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)

class Meeting(Base):
    __tablename__ = "meeting"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    title: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    failed_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

class Recording(Base):
    __tablename__ = "recording"
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id", ondelete="CASCADE"))
    raw_key: Mapped[str] = mapped_column(String(500))
    normalized_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

class Participant(Base):
    __tablename__ = "participant"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_participant_org_name"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    name: Mapped[str] = mapped_column(String(200))
