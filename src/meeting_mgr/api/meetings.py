import json

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import (Attribution, Meeting, Organization,
                                Participant, Recording, Segment, SpeakerCluster,
                                KeyTopic, Minute, ActionItem, DecisionPoint)
from meeting_mgr.progress import subscribe
from meeting_mgr.storage import (RangeNotSatisfiable, ensure_bucket,
                                 open_object, put_stream)

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
    SpeakerCluster: ("id", "label", "spans"),
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

@router.get("/meetings")
def list_meetings():
    with get_readonly_session() as s:
        rows = s.query(Meeting).order_by(Meeting.id.desc()).all()
        return [
            {"id": m.id, "title": m.title, "status": m.status,
             "current_stage": m.current_stage, "failed_stage": m.failed_stage,
             "created_at": m.created_at}
            for m in rows
        ]

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
            "segments": rows(Segment), "clusters": rows(SpeakerCluster),
            "attributions": [
                {"cluster_id": a.cluster_id, "participant_id": a.participant_id,
                 "participant_name": s.get(Participant, a.participant_id).name,
                 "provenance": a.provenance}
                for a in s.query(Attribution).join(SpeakerCluster)
                          .filter(SpeakerCluster.meeting_id == meeting_id).all()
            ],
            "key_topics": rows(KeyTopic),
            "minutes": rows(Minute), "action_items": rows(ActionItem),
            "decision_points": rows(DecisionPoint),
        }

@router.get("/meetings/{meeting_id}/events")
def stream_events(meeting_id: int):
    with get_readonly_session() as s:
        m = s.get(Meeting, meeting_id)
        if m is None:
            raise HTTPException(404, "meeting not found")
        snapshot = {"status": m.status, "current_stage": m.current_stage,
                    "failed_stage": m.failed_stage}

    def events():
        # A client that connects mid-run, or reloads, missed every prior
        # event — Redis pub/sub has no backlog. The snapshot is what makes
        # reconnect and refresh work.
        yield f"data: {json.dumps(snapshot)}\n\n"
        if snapshot["status"] in ("published", "failed"):
            return
        for event in subscribe(meeting_id):
            yield f"data: {json.dumps(event)}\n\n"
            finished_publish = (event.get("stage") == "publish" and
                                event.get("state") == "finished")
            if finished_publish or event.get("state") == "failed":
                return

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache",
                                      "x-accel-buffering": "no"})

def _valid_range(header: str | None) -> str | None:
    """Pass through only the forms browsers send for media seeking.

    Anything else returns None, which serves the whole object. A media element
    that receives the full file still works; one that receives a 400 does not.
    """
    if not header or not header.startswith("bytes="):
        return None
    start_s, sep, end_s = header.removeprefix("bytes=").partition("-")
    if not sep or not start_s.isdigit():
        return None
    if end_s and not end_s.isdigit():
        return None
    return header

def _chunks(stream, size: int = 1 << 16):
    """Yield the body in chunks so an hour of audio never lands in memory."""
    try:
        while chunk := stream.read(size):
            yield chunk
    finally:
        stream.close()

@router.get("/meetings/{meeting_id}/audio")
def read_audio(meeting_id: int, request: Request):
    with get_readonly_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none()
        key = rec.normalized_key if rec else None
    if not key:
        raise HTTPException(404, "no normalized audio for this meeting")

    try:
        stream, content_range, length = open_object(
            key, _valid_range(request.headers.get("range")))
    except RangeNotSatisfiable:
        raise HTTPException(416, "range not satisfiable")

    headers = {"accept-ranges": "bytes", "content-length": str(length)}
    if content_range:
        headers["content-range"] = content_range
    return StreamingResponse(
        _chunks(stream), status_code=206 if content_range else 200,
        media_type="audio/wav", headers=headers,
    )
