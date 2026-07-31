from meeting_mgr.db import get_session
from meeting_mgr.models import Attribution, Participant, Segment, SpeakerCluster
from meeting_mgr.pipeline.attribute import AttributionProposal, attribute, render_transcript


def _seed(mid):
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        s.add(
            Segment(
                meeting_id=mid,
                cluster_id=c.id,
                start_seconds=0.0,
                end_seconds=1.0,
                text="thanks Sarah",
            )
        )


def test_render_transcript_labels_speakers(make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    assert render_transcript(mid) == "[SPEAKER_00] thanks Sarah"


def test_attribute_creates_inferred_attribution(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": "Sarah"}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        a = s.query(Attribution).join(SpeakerCluster).filter(SpeakerCluster.meeting_id == mid).one()
        assert a.provenance == "inferred"
        assert s.get(Participant, a.participant_id).name == "Sarah"


def test_attribute_skips_unnamed_clusters(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": None}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        assert (
            s.query(Attribution)
            .join(SpeakerCluster)
            .filter(SpeakerCluster.meeting_id == mid)
            .count()
            == 0
        )


def test_attribute_skips_whitespace_only_names(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": "   "}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        assert (
            s.query(Attribution)
            .join(SpeakerCluster)
            .filter(SpeakerCluster.meeting_id == mid)
            .count()
        ) == 0, "a whitespace-only name identifies nobody and must not be attributed"
