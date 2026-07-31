from sqlalchemy import ForeignKey, Float, String, JSON
from sqlalchemy.orm import Mapped, mapped_column
from meeting_mgr.db import Base

class SpeakerCluster(Base):
    __tablename__ = "speaker_cluster"
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(50))          # e.g. "SPEAKER_00"
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    spans: Mapped[list] = mapped_column(JSON, default=list)

class Segment(Base):
    __tablename__ = "segment"
    id: Mapped[int] = mapped_column(primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meeting.id", ondelete="CASCADE"))
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("speaker_cluster.id", ondelete="SET NULL"), nullable=True)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(String)

class Attribution(Base):
    __tablename__ = "attribution"
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("speaker_cluster.id", ondelete="CASCADE"))
    participant_id: Mapped[int] = mapped_column(ForeignKey("participant.id", ondelete="CASCADE"))
    provenance: Mapped[str] = mapped_column(String(20), default="unknown")
