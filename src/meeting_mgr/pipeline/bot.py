"""Bot session liveness sweep: a bot process that crashes mid-call, or is
silently ejected, leaves its Meeting stuck in "capturing" forever with no
other signal. This task is the "how does an operator know" answer for that
failure mode -- the same heartbeat-and-sweep shape Phase 5's
WatchFolder.last_scan_at/stalled established for a stalled watcher.
"""

import logging
from datetime import datetime, timedelta

from meeting_mgr.audit import record_audit
from meeting_mgr.db import get_org_session, get_readonly_session
from meeting_mgr.models import BotSession, Meeting
from meeting_mgr.pipeline.app import celery_app
from meeting_mgr.pipeline.bot_config import STALE_SESSION_SECONDS

logger = logging.getLogger(__name__)


@celery_app.task(name="meeting_mgr.sweep_stale_bot_sessions")
def sweep_stale_bot_sessions(now: datetime | None = None) -> None:
    """Untenanted read (get_readonly_session) to find candidates -- same
    narrow exception Phase 4's sweep_retention and Phase 5's
    scan_watch_folders carved out: this reads only BotSession/Meeting
    identity columns to decide what to sweep, never Transcript content, and
    writes nothing on this connection. Each write happens on its own
    get_org_session(organization_id), isolated so one Meeting failing to
    update does not block the rest of the sweep -- and so the sweep spans
    every organization with a stale session, not just one tenant.

    `now` is injectable so tests can simulate staleness without sleeping or
    backdating rows past what a fresh INSERT would otherwise assign -- same
    shape as retention.select_purge_candidates(..., now=None)."""
    cutoff = (now or datetime.utcnow()) - timedelta(seconds=STALE_SESSION_SECONDS)
    with get_readonly_session() as s:
        rows = (
            s.query(BotSession.id, BotSession.organization_id, BotSession.meeting_id)
            .join(Meeting, Meeting.id == BotSession.meeting_id)
            .filter(Meeting.status == "capturing", BotSession.last_activity_at <= cutoff)
            .all()
        )

    for session_id, organization_id, meeting_id in rows:
        try:
            with get_org_session(organization_id) as s:
                m = s.get(Meeting, meeting_id)
                if m is not None and m.status == "capturing":
                    m.status, m.failed_stage = "failed", "bot_ingest"
                    record_audit(
                        s,
                        organization_id=organization_id,
                        actor_account_id=None,
                        action="meeting.bot_ingest.stale",
                        target=f"meeting:{meeting_id}",
                        detail={"bot_session_id": session_id},
                    )
        except Exception:
            # Swallowed on purpose -- one Meeting failing to update must
            # not withhold the rest of the sweep. Logged so a Meeting that
            # fails every sweep is visible to an operator instead of
            # silently never being marked failed.
            logger.exception(
                "sweep_stale_bot_sessions: failed to mark meeting_id=%s organization_id=%s failed",
                meeting_id,
                organization_id,
            )
            continue
