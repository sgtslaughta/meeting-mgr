from datetime import date
from sqlalchemy import ForeignKey, String, JSON, Boolean, Date
from sqlalchemy.orm import Mapped, mapped_column
from meeting_mgr.db import Base

class _Derived:
    """Mixin: every derived fact cites Segments and declares its Provenance."""
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id", ondelete="CASCADE"))
    citations: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[str] = mapped_column(String(20), default="inferred")

class KeyTopic(_Derived, Base):
    __tablename__ = "key_topic"
    title: Mapped[str] = mapped_column(String(300))

class Minute(_Derived, Base):
    __tablename__ = "minute"
    text: Mapped[str] = mapped_column(String)

class ActionItem(_Derived, Base):
    __tablename__ = "action_item"
    text: Mapped[str] = mapped_column(String)
    participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("participant.id", ondelete="SET NULL"), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")

class DecisionPoint(_Derived, Base):
    __tablename__ = "decision_point"
    text: Mapped[str] = mapped_column(String)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    positions: Mapped[list] = mapped_column(JSON, default=list)  # [{participant_id, position}]
