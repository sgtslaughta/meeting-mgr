from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import (Meeting, Organization, Recording, Segment,
                                KeyTopic, Minute, ActionItem, DecisionPoint)
from meeting_mgr.storage import ensure_bucket, put_stream

router = APIRouter()

# Explicit per-model field allowlists. Serializing __table__.columns would
# auto-publish every column later phases add (auth, audit, retention), so
# exposing a new field must be a deliberate edit here.
_FIELDS = {
    Segment: ("id", "start_seconds", "end_seconds", "text", "cluster_id"),
    KeyTopic: ("id", "title", "citations", "provenance"),
    Minute: ("id", "text", "citations", "provenance"),
    ActionItem: ("id", "text", "participant_id", "due_date", "status",
                 "citations", "provenance"),
    DecisionPoint: ("id", "text", "settled", "positions", "citations",
                    "provenance"),
}

def run_pipeline(meeting_id: int) -> None:
    """Module-level indirection on purpose.

    The import is deferred to call time so this module does not depend on
    meeting_mgr.pipeline.orchestrate existing yet (it is built in Task 13),
    and so tests can monkeypatch meeting_mgr.api.meetings.run_pipeline.
    """
    from meeting_mgr.pipeline.orchestrate import run_pipeline as task
    task.delay(meeting_id)

@router.post("/meetings", status_code=201)
def create_meeting(title: str = Form(...), file: UploadFile = File(...)):
    ensure_bucket()
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title=title, status="pending")
        s.add(m); s.flush()
        key = f"raw/{m.id}/{file.filename}"
        put_stream(key, file.file)
        s.add(Recording(meeting_id=m.id, raw_key=key))
        meeting_id = m.id
    run_pipeline(meeting_id)
    return {"meeting_id": meeting_id, "status": "pending"}

@router.get("/meetings/{meeting_id}")
def read_meeting(meeting_id: int):
    with get_readonly_session() as s:
        m = s.get(Meeting, meeting_id)
        if m is None:
            raise HTTPException(404, "meeting not found")
        def rows(model):
            fields = _FIELDS[model]
            return [
                {f: getattr(r, f) for f in fields}
                for r in s.query(model).filter_by(meeting_id=meeting_id).all()
            ]
        return {
            "id": m.id, "title": m.title, "status": m.status,
            "failed_stage": m.failed_stage,
            "segments": rows(Segment), "key_topics": rows(KeyTopic),
            "minutes": rows(Minute), "action_items": rows(ActionItem),
            "decision_points": rows(DecisionPoint),
        }
