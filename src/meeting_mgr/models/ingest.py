from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from meeting_mgr.db import Base


class WatchFolder(Base):
    """Admin-configured mapping from a filesystem path (inside the worker
    container) to the Organization and Account a dropped Recording belongs
    to. No path is ever inferred from directory naming -- see Global
    Constraints in the Phase 5 plan. last_scan_at/last_scan_error are the
    heartbeat GET /watch-folders surfaces to an operator."""

    __tablename__ = "watch_folder"
    __table_args__ = (
        UniqueConstraint("organization_id", "root_path", name="uq_watch_folder_org_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id"))
    owner_account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    root_path: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_scan_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
