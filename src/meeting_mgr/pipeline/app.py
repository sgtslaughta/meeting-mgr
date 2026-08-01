from celery import Celery

from meeting_mgr.config import get_settings
from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting
from meeting_mgr.pipeline.bot_config import BOT_SWEEP_INTERVAL_SECONDS
from meeting_mgr.pipeline.watch_config import SCAN_INTERVAL_SECONDS

celery_app = Celery(
    "meeting_mgr",
    broker=get_settings().redis_url,
    # Without this, `celery -A meeting_mgr.pipeline.app worker` never learns
    # these modules exist and silently discards every task it receives as
    # "unregistered" -- it did, in production, from Phase 1 until this fix.
    # The compose -I flag is now redundant but kept as belt-and-braces.
    # pipeline.watch imports celery_app from THIS module, not the reverse --
    # include is a list of module names Celery imports lazily at worker
    # startup, not a Python `import` statement evaluated here, so listing
    # it does not make this module load pipeline/watch.py itself. No cycle.
    include=[
        "meeting_mgr.pipeline.orchestrate",
        "meeting_mgr.api.edits",
        "meeting_mgr.pipeline.purge",
        "meeting_mgr.pipeline.watch",
        "meeting_mgr.pipeline.bot",
    ],
)
celery_app.conf.update(
    task_acks_late=True,  # a lost worker must not lose an hour of GPU work
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 7200},
)
celery_app.conf.beat_schedule = {
    "sweep-retention-daily": {
        "task": "meeting_mgr.sweep_retention",
        "schedule": 86400.0,  # once per day; run by the "beat" compose service (Task 12)
    },
    "scan-watch-folders-periodic": {
        "task": "meeting_mgr.scan_watch_folders",
        # Same constant api/watch_folders.py's stalled-flag threshold
        # derives from (2x this) -- a literal here would let the two drift
        # apart, making a healthy watcher eventually read as dead.
        "schedule": float(SCAN_INTERVAL_SECONDS),
    },
    "sweep-stale-bot-sessions": {
        "task": "meeting_mgr.sweep_stale_bot_sessions",
        "schedule": float(BOT_SWEEP_INTERVAL_SECONDS),
    },
}


def set_stage_failure(meeting_id: int, stage: str) -> None:
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        m.status, m.failed_stage = "failed", stage
