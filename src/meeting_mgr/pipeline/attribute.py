from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.inference.llm import structured_chat
from meeting_mgr.models import (Attribution, Meeting, Participant, Segment,
                                SpeakerCluster)

class ClusterName(BaseModel):
    label: str
    name: str | None = None

class AttributionProposal(BaseModel):
    names: list[ClusterName]

PROMPT = """Below is a meeting transcript with anonymous speaker labels.
Using only in-transcript cues (people addressing each other by name,
self-introductions), propose a real name for each label.
Use null when the transcript gives no evidence. Never guess.

Return JSON: {{"names": [{{"label": "SPEAKER_00", "name": "Sarah"}}]}}

TRANSCRIPT:
{transcript}"""

def render_transcript(meeting_id: int) -> str:
    with get_readonly_session() as s:
        rows = (
            s.query(Segment, SpeakerCluster)
            .outerjoin(SpeakerCluster, Segment.cluster_id == SpeakerCluster.id)
            .filter(Segment.meeting_id == meeting_id)
            .order_by(Segment.start_seconds).all()
        )
        return "\n".join(
            f"[{c.label if c else 'UNKNOWN'}] {seg.text}" for seg, c in rows
        )

def attribute(meeting_id: int) -> None:
    transcript = render_transcript(meeting_id)
    proposal = structured_chat(PROMPT.format(transcript=transcript), AttributionProposal)
    with get_session() as s:
        org_id = s.get(Meeting, meeting_id).organization_id
        by_label = {c.label: c for c in
                    s.query(SpeakerCluster).filter_by(meeting_id=meeting_id).all()}
        for proposed in proposal.names:
            cluster = by_label.get(proposed.label)
            if cluster is None or not proposed.name:
                continue
            participant = (s.query(Participant)
                            .filter_by(organization_id=org_id, name=proposed.name)
                            .one_or_none())
            if participant is None:
                try:
                    # SAVEPOINT: if a concurrent worker wins the race, only
                    # this insert is rolled back, not the whole session's work.
                    with s.begin_nested():
                        participant = Participant(organization_id=org_id, name=proposed.name)
                        s.add(participant)
                except IntegrityError:
                    participant = (s.query(Participant)
                                    .filter_by(organization_id=org_id, name=proposed.name)
                                    .one())
            s.add(Attribution(cluster_id=cluster.id,
                              participant_id=participant.id,
                              provenance="inferred"))
