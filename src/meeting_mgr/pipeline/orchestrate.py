from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting
from meeting_mgr.pipeline.align import align
from meeting_mgr.pipeline.app import celery_app, set_stage_failure
from meeting_mgr.pipeline.attribute import attribute
from meeting_mgr.pipeline.diarize import diarize
from meeting_mgr.pipeline.extract import (
    extract_action_items,
    extract_decision_points,
    extract_key_topics,
    extract_minutes,
)
from meeting_mgr.pipeline.normalize import normalize
from meeting_mgr.pipeline.transcribe import transcribe
from meeting_mgr.progress import publish as publish_progress

# Extraction stages are best-effort: a Meeting with a Transcript but no Action
# Items is still useful, so one failed artifact must not withhold the rest.
OPTIONAL_STAGES = {"key_topics", "minutes", "action_items", "decision_points"}


def publish(meeting_id: int) -> None:
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        m.status = "published"


STAGES = [
    ("normalize", normalize),
    ("diarize", diarize),
    ("transcribe", transcribe),
    ("align", align),
    ("attribute", attribute),
    ("key_topics", extract_key_topics),
    ("minutes", extract_minutes),
    ("action_items", extract_action_items),
    ("decision_points", extract_decision_points),
    ("publish", publish),
]


def _set_current_stage(meeting_id: int, stage: str | None) -> None:
    with get_session() as s:
        s.get(Meeting, meeting_id).current_stage = stage


@celery_app.task(name="meeting_mgr.run_pipeline")
def run_pipeline(meeting_id: int, from_stage: str | None = None) -> None:
    names = [n for n, _ in STAGES]
    if from_stage and from_stage not in names:
        raise ValueError(f"unknown stage {from_stage!r}; expected one of {names}")
    start = names.index(from_stage) if from_stage else 0
    for name, fn in STAGES[start:]:
        _set_current_stage(meeting_id, name)
        publish_progress(meeting_id, name, "started")
        try:
            fn(meeting_id)
        except Exception:
            set_stage_failure(meeting_id, name)
            publish_progress(meeting_id, name, "failed")
            if name in OPTIONAL_STAGES:
                continue
            _set_current_stage(meeting_id, None)
            raise
        publish_progress(meeting_id, name, "finished")
    _set_current_stage(meeting_id, None)
