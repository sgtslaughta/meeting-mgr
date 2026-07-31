from pydantic import BaseModel
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.inference.llm import structured_chat
from meeting_mgr.models import (ActionItem, DecisionPoint, KeyTopic, Meeting,
                                Minute, Participant, Segment, SpeakerCluster)

class TopicOut(BaseModel):
    title: str
    citations: list[int]

class TopicsOut(BaseModel):
    topics: list[TopicOut]

class MinuteOut(BaseModel):
    text: str
    citations: list[int]

class MinutesOut(BaseModel):
    minutes: list[MinuteOut]

class ActionItemOut(BaseModel):
    text: str
    participant_name: str | None = None
    citations: list[int]

class ActionItemsOut(BaseModel):
    action_items: list[ActionItemOut]

class PositionOut(BaseModel):
    participant_name: str
    position: str

class DecisionPointOut(BaseModel):
    text: str
    settled: bool
    positions: list[PositionOut] = []
    citations: list[int]

class DecisionPointsOut(BaseModel):
    decision_points: list[DecisionPointOut]

_HEADER = """Each transcript line starts with [segment_id][SPEAKER].
Every item you return MUST cite the segment_ids it came from.
Never cite a segment_id that is not in the transcript.

"""

def render_cited_transcript(meeting_id: int) -> str:
    with get_readonly_session() as s:
        rows = (
            s.query(Segment, SpeakerCluster)
            .outerjoin(SpeakerCluster, Segment.cluster_id == SpeakerCluster.id)
            .filter(Segment.meeting_id == meeting_id)
            .order_by(Segment.start_seconds).all()
        )
        return "\n".join(
            f"[{seg.id}][{c.label if c else 'UNKNOWN'}] {seg.text}" for seg, c in rows
        )

def _ask(meeting_id: int, instruction: str, schema: type[BaseModel]) -> BaseModel:
    prompt = _HEADER + instruction + "\n\nTRANSCRIPT:\n" + render_cited_transcript(meeting_id)
    return structured_chat(prompt, schema)

def _valid_segment_ids(meeting_id: int) -> set[int]:
    with get_readonly_session() as s:
        return {sid for (sid,) in
                s.query(Segment.id).filter(Segment.meeting_id == meeting_id).all()}

def _cited(citations: list[int], valid: set[int]) -> list[int]:
    return [c for c in citations if c in valid]

def _resolve_participant(s, org_id: int, name: str | None) -> int | None:
    """Same (organization_id, name) rule the attribution stage uses, so a name
    resolves to one Participant however it was discovered."""
    if not name or not name.strip():
        return None
    p = (s.query(Participant)
          .filter_by(organization_id=org_id, name=name).one_or_none())
    if p is None:
        p = Participant(organization_id=org_id, name=name)
        s.add(p); s.flush()
    return p.id

def extract_key_topics(meeting_id: int) -> None:
    out = _ask(meeting_id,
               'List the subjects the meeting spent meaningful time on. '
               'Return JSON: {"topics": [{"title": "...", "citations": [1]}]}',
               TopicsOut)
    valid = _valid_segment_ids(meeting_id)
    with get_session() as s:
        for t in out.topics:
            citations = _cited(t.citations, valid)
            if not citations:
                continue
            s.add(KeyTopic(meeting_id=meeting_id, title=t.title,
                           citations=citations, provenance="inferred"))

def extract_minutes(meeting_id: int) -> None:
    out = _ask(meeting_id,
               'Write narrative minutes, one entry per distinct point. '
               'Return JSON: {"minutes": [{"text": "...", "citations": [1]}]}',
               MinutesOut)
    valid = _valid_segment_ids(meeting_id)
    with get_session() as s:
        for m in out.minutes:
            citations = _cited(m.citations, valid)
            if not citations:
                continue
            s.add(Minute(meeting_id=meeting_id, text=m.text,
                         citations=citations, provenance="inferred"))

def extract_action_items(meeting_id: int) -> None:
    out = _ask(meeting_id,
               'List commitments someone made to do something. Use null for '
               'participant_name when unclear. Return JSON: {"action_items": '
               '[{"text": "...", "participant_name": "Sarah", "citations": [1]}]}',
               ActionItemsOut)
    valid = _valid_segment_ids(meeting_id)
    with get_session() as s:
        org_id = s.get(Meeting, meeting_id).organization_id
        for a in out.action_items:
            citations = _cited(a.citations, valid)
            if not citations:
                continue
            participant_id = None
            if a.participant_name:
                participant_id = _resolve_participant(s, org_id, a.participant_name)
            s.add(ActionItem(meeting_id=meeting_id, text=a.text,
                             participant_id=participant_id,
                             citations=citations, provenance="inferred"))

def extract_decision_points(meeting_id: int) -> None:
    out = _ask(meeting_id,
               'List places the meeting resolved something or failed to. Set '
               'settled=false when the matter was left contested. Record each '
               'position taken and who held it. Return JSON: {"decision_points": '
               '[{"text": "...", "settled": true, "positions": '
               '[{"participant_name": "Sarah", "position": "..."}], "citations": [1]}]}',
               DecisionPointsOut)
    valid = _valid_segment_ids(meeting_id)
    with get_session() as s:
        org_id = s.get(Meeting, meeting_id).organization_id
        for d in out.decision_points:
            citations = _cited(d.citations, valid)
            if not citations:
                continue
            positions = []
            for pos in d.positions:
                pid = _resolve_participant(s, org_id, pos.participant_name)
                if pid is None:
                    continue   # a position with no identifiable holder is not a position
                positions.append({"participant_id": pid, "position": pos.position})
            s.add(DecisionPoint(meeting_id=meeting_id, text=d.text,
                                settled=d.settled, positions=positions,
                                citations=citations, provenance="inferred"))
