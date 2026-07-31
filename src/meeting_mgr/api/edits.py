from datetime import date

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from meeting_mgr.api.meetings import _FIELDS
from meeting_mgr.db import get_session
from meeting_mgr.models import (
    ActionItem,
    Attribution,
    DecisionPoint,
    KeyTopic,
    Meeting,
    Minute,
    SpeakerCluster,
)
from meeting_mgr.participants import resolve_participant
from meeting_mgr.pipeline.app import celery_app
from meeting_mgr.pipeline.extract import (
    extract_action_items,
    extract_decision_points,
    extract_key_topics,
    extract_minutes,
)
from meeting_mgr.provenance import confirm

router = APIRouter()


class AttributionIn(BaseModel):
    participant_name: str | None = None


@router.patch("/meetings/{meeting_id}/clusters/{cluster_id}")
def confirm_attribution(meeting_id: int, cluster_id: int, body: AttributionIn):
    with get_session() as s:
        cluster = (
            s.query(SpeakerCluster).filter_by(id=cluster_id, meeting_id=meeting_id).one_or_none()
        )
        if cluster is None:
            raise HTTPException(404, "cluster not found in this meeting")

        org_id = s.get(Meeting, meeting_id).organization_id
        # Replace rather than accumulate: a cluster has one holder.
        s.query(Attribution).filter_by(cluster_id=cluster_id).delete()

        participant_id = resolve_participant(s, org_id, body.participant_name)
        if participant_id is None:
            # No fact survives: the absence of an Attribution row IS "nobody
            # has decided yet". Nothing to confirm, nothing to label.
            return {
                "cluster_id": cluster_id,
                "participant_id": None,
                "participant_name": None,
                "provenance": None,
            }

        attribution = Attribution(cluster_id=cluster_id, participant_id=participant_id)
        confirm(attribution)
        s.add(attribution)
        return {
            "cluster_id": cluster_id,
            "participant_id": participant_id,
            "participant_name": body.participant_name,
            "provenance": "confirmed",
        }


# url segment -> (model, fields a human may change). Never add "provenance" or
# "citations" here: those are the record of where a claim came from and who
# decided it, and this set is the only gate a PATCH body passes through.
_ARTIFACTS = {
    "key_topics": (KeyTopic, {"title"}),
    "minutes": (Minute, {"text"}),
    "action_items": (ActionItem, {"text", "status", "due_date", "participant_name"}),
    "decision_points": (DecisionPoint, {"text", "settled"}),
}


def _lookup(s, meeting_id: int, artifact_type: str, item_id: int):
    if artifact_type not in _ARTIFACTS:
        raise HTTPException(404, f"unknown artifact type {artifact_type!r}")
    model, editable = _ARTIFACTS[artifact_type]
    row = s.query(model).filter_by(id=item_id, meeting_id=meeting_id).one_or_none()
    if row is None:
        raise HTTPException(404, "item not found in this meeting")
    return row, model, editable


@router.patch("/meetings/{meeting_id}/{artifact_type}/{item_id}")
def edit_artifact(meeting_id: int, artifact_type: str, item_id: int, body: dict):
    with get_session() as s:
        row, model, editable = _lookup(s, meeting_id, artifact_type, item_id)
        unknown = set(body) - editable
        if unknown:
            # citations and provenance are never client-writable: they are the
            # record of where a claim came from and who decided it.
            raise HTTPException(400, f"not editable: {sorted(unknown)}")

        for field, value in body.items():
            if field == "participant_name":
                org_id = s.get(Meeting, meeting_id).organization_id
                row.participant_id = resolve_participant(s, org_id, value)
            elif field == "due_date":
                row.due_date = date.fromisoformat(value) if value else None
            else:
                setattr(row, field, value)
        confirm(row)
        s.flush()
        # Same allowlist the read endpoint uses — a PATCH response must not
        # become the back door that publishes a future auth or audit column.
        return {f: getattr(row, f) for f in _FIELDS[model]}


@router.delete("/meetings/{meeting_id}/{artifact_type}/{item_id}", status_code=204)
def delete_artifact(meeting_id: int, artifact_type: str, item_id: int):
    with get_session() as s:
        row, _, _ = _lookup(s, meeting_id, artifact_type, item_id)
        s.delete(row)
    return Response(status_code=204)


# artifact_type -> the extraction pass that repopulates it. A dict of function
# *objects* would freeze a reference at import time, so monkeypatching
# meeting_mgr.api.edits.extract_key_topics (as tests, and any future caller
# swapping the extractor, do) would silently keep calling the old one. Bare
# name lookup below resolves in this module's globals() at call time instead.
_REGENERATABLE = {"key_topics", "minutes", "action_items", "decision_points"}


def _run_extraction(artifact_type: str, meeting_id: int) -> None:
    if artifact_type == "key_topics":
        extract_key_topics(meeting_id)
    elif artifact_type == "minutes":
        extract_minutes(meeting_id)
    elif artifact_type == "action_items":
        extract_action_items(meeting_id)
    elif artifact_type == "decision_points":
        extract_decision_points(meeting_id)


@celery_app.task(name="meeting_mgr.regenerate_artifact")
def _regenerate_task(meeting_id: int, artifact_type: str) -> None:
    _run_extraction(artifact_type, meeting_id)


@router.post("/meetings/{meeting_id}/regenerate/{artifact_type}", status_code=202)
def regenerate_artifact(meeting_id: int, artifact_type: str):
    if artifact_type not in _REGENERATABLE:
        raise HTTPException(404, f"unknown artifact type {artifact_type!r}")
    model, _ = _ARTIFACTS[artifact_type]
    with get_session() as s:
        if s.get(Meeting, meeting_id) is None:
            raise HTTPException(404, "meeting not found")
        # Delete before enqueueing: a failed re-run must leave the category
        # empty, not stale-but-plausible for a human to mistake as fresh.
        s.query(model).filter_by(meeting_id=meeting_id).delete()
    _regenerate_task.delay(meeting_id, artifact_type)
    return {"regenerated": artifact_type}
