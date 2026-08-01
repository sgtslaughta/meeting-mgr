"""Bot session liveness sweep: a bot process that crashes mid-call, or is
silently ejected, leaves its Meeting stuck in "capturing" forever with no
other signal. This task is the "how does an operator know" answer for that
failure mode -- the same heartbeat-and-sweep shape Phase 5's
WatchFolder.last_scan_at/stalled established for a stalled watcher.

Also sweeps "finishing" -- api/bot.py::finish_session commits that status
as its FIRST step (see its docstring), then does the manifest-building work
in a second, later transaction. A crash between those two leaves the
Meeting in "finishing" with no other code path able to move it: a retried
finish() 409s (status != "capturing"), and this sweep's own pre-fix filter
(status == "capturing" only) never selected it. This is the sole recovery
path for that state -- see finish_session's docstring for why client-side
retry was rejected in favor of this sweep instead.
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
            .filter(
                # "finishing" reuses the same BotSession.last_activity_at
                # heartbeat as "capturing": finish_session never touches
                # last_activity_at, so by the time a Meeting has been
                # sitting in "finishing" for this long, the value is
                # already stale from the last chunk upload before finish()
                # was ever called -- no separate timestamp is needed.
                Meeting.status.in_(("capturing", "finishing")),
                BotSession.last_activity_at <= cutoff,
            )
            .all()
        )

    for session_id, organization_id, meeting_id in rows:
        try:
            with get_org_session(organization_id) as s:
                m = s.get(Meeting, meeting_id)
                if m is None:
                    continue
                # Re-check right before writing, same reasoning as before:
                # a Meeting that legitimately left "capturing"/"finishing"
                # between the untenanted read above and this write (e.g. a
                # finish() call that completes normally in between) must
                # not be raced by this sweep.
                if m.status == "capturing":
                    m.status, m.failed_stage = "failed", "bot_ingest"
                    record_audit(
                        s,
                        organization_id=organization_id,
                        actor_account_id=None,
                        action="meeting.bot_ingest.stale",
                        target=f"meeting:{meeting_id}",
                        detail={"bot_session_id": session_id},
                    )
                elif m.status == "finishing":
                    # Failed out, not resumed: a half-built manifest (some
                    # chunks may have landed after the flip, some may not
                    # have) is not obviously safe to auto-complete from a
                    # background sweep with no request context, and this
                    # mirrors finish_session's own empty-chunk case (also a
                    # terminal "failed", never a silent retry). A distinct
                    # failed_stage from "bot_ingest" so an operator can tell
                    # "never made it past its first chunk" apart from
                    # "crashed while finishing, chunks may be orphaned in
                    # storage."
                    m.status, m.failed_stage = "failed", "bot_ingest_finish_stuck"
                    record_audit(
                        s,
                        organization_id=organization_id,
                        actor_account_id=None,
                        action="meeting.bot_ingest.finish_stuck",
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
