from celery import Celery
from meeting_mgr.config import get_settings
from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting

celery_app = Celery("meeting_mgr", broker=get_settings().redis_url)
celery_app.conf.update(
    task_acks_late=True,                # a lost worker must not lose an hour of GPU work
    task_reject_on_worker_lost=True,
    broker_transport_options={"visibility_timeout": 7200},
)

def set_stage_failure(meeting_id: int, stage: str) -> None:
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        m.status, m.failed_stage = "failed", stage
