import pytest

from meeting_mgr.db import get_session
from meeting_mgr.models import (
    ActionItem,
    DecisionPoint,
    KeyTopic,
    Minute,
    Organization,
    Participant,
    Segment,
    SpeakerCluster,
)
from meeting_mgr.pipeline import extract as ex


def _seed(mid) -> int:
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        seg = Segment(
            meeting_id=mid,
            cluster_id=c.id,
            start_seconds=0.0,
            end_seconds=1.0,
            text="I will ship the migration",
        )
        s.add(seg)
        s.flush()
        return seg.id


def test_render_cited_transcript_includes_segment_ids(make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    assert ex.render_cited_transcript(mid) == f"[{sid}][SPEAKER_00] I will ship the migration"


def test_extract_key_topics_stores_citations(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"topics": [{"title": "migration", "citations": [sid]}]}
        ),
    )
    ex.extract_key_topics(mid)
    with get_session() as s:
        t = s.query(KeyTopic).filter_by(meeting_id=mid).one()
        assert t.title == "migration"
        assert t.citations == [sid]
        assert t.provenance == "inferred"


def test_extract_action_items_links_participant(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {
                "action_items": [
                    {"text": "ship the migration", "participant_name": "Sarah", "citations": [sid]}
                ]
            }
        ),
    )
    ex.extract_action_items(mid)
    with get_session() as s:
        item = s.query(ActionItem).filter_by(meeting_id=mid).one()
        assert item.participant_id is not None
        assert item.citations == [sid]


def test_extract_drops_items_whose_citations_are_all_invalid(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"topics": [{"title": "real", "citations": [999999]}]}
        ),
    )
    ex.extract_key_topics(mid)
    with get_session() as s:
        assert s.query(KeyTopic).filter_by(meeting_id=mid).count() == 0


def test_extract_keeps_only_the_valid_citations(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"topics": [{"title": "mixed", "citations": [sid, 999999]}]}
        ),
    )
    ex.extract_key_topics(mid)
    with get_session() as s:
        assert s.query(KeyTopic).filter_by(meeting_id=mid).one().citations == [sid]


def test_extract_minutes_stores_citations(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"minutes": [{"text": "Sarah committed", "citations": [sid]}]}
        ),
    )
    ex.extract_minutes(mid)
    with get_session() as s:
        m = s.query(Minute).filter_by(meeting_id=mid).one()
        assert m.text == "Sarah committed"
        assert m.citations == [sid]
        assert m.provenance == "inferred"


def test_extract_decision_points_records_positions(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {
                "decision_points": [
                    {
                        "text": "ship now or wait",
                        "settled": False,
                        "positions": [
                            {"participant_name": "Sarah", "position": "ship now"},
                            {"participant_name": "Raj", "position": "wait"},
                        ],
                        "citations": [sid],
                    }
                ]
            }
        ),
    )
    ex.extract_decision_points(mid)
    with get_session() as s:
        d = s.query(DecisionPoint).filter_by(meeting_id=mid).one()
        assert d.settled is False
        assert d.citations == [sid]
        assert len(d.positions) == 2
        names = {
            s.get(Participant, pos["participant_id"]).name: pos["position"] for pos in d.positions
        }
        assert names == {"Sarah": "ship now", "Raj": "wait"}


def test_decision_point_drops_positions_with_no_named_holder(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {
                "decision_points": [
                    {
                        "text": "ship now or wait",
                        "settled": False,
                        "positions": [
                            {"participant_name": "Sarah", "position": "ship now"},
                            {"participant_name": "", "position": "wait"},
                            {"participant_name": "   ", "position": "abstain"},
                        ],
                        "citations": [sid],
                    }
                ]
            }
        ),
    )
    ex.extract_decision_points(mid)
    with get_session() as s:
        d = s.query(DecisionPoint).filter_by(meeting_id=mid).one()
        assert len(d.positions) == 1, "blank-named positions must be dropped"
        assert s.get(Participant, d.positions[0]["participant_id"]).name == "Sarah"


def test_no_participant_is_created_for_a_blank_name(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    sid = _seed(mid)
    with get_session() as s:
        before = s.query(Participant).count()
    monkeypatch.setattr(
        ex,
        "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {
                "decision_points": [
                    {
                        "text": "unresolved",
                        "settled": False,
                        "positions": [{"participant_name": "", "position": "wait"}],
                        "citations": [sid],
                    }
                ]
            }
        ),
    )
    ex.extract_decision_points(mid)
    with get_session() as s:
        assert s.query(Participant).count() == before, (
            "a blank participant_name must not create a Participant row"
        )


def test_participant_name_is_unique_per_organization():
    from sqlalchemy.exc import IntegrityError

    name = "Duplicate Test Person"
    with get_session() as s:
        org_id = s.query(Organization).filter_by(name="default").one().id
    try:
        # Clean first: this test must pass on a database it has already run against.
        with get_session() as s:
            s.query(Participant).filter_by(organization_id=org_id, name=name).delete()
        with get_session() as s:
            s.add(Participant(organization_id=org_id, name=name))
        with pytest.raises(IntegrityError):
            with get_session() as s:
                s.add(Participant(organization_id=org_id, name=name))
    finally:
        with get_session() as s:
            s.query(Participant).filter_by(organization_id=org_id, name=name).delete()


def test_render_cited_transcript_breaks_start_seconds_ties_on_segment_id(make_meeting):
    """Citation anchors make stable ordering load-bearing here: an unstable
    order makes the [seg.id] anchors the model returns unreproducible
    between runs on identical input. See test_attribute.py for why the
    UPDATE below is what gives this test the power to fail."""
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        first = Segment(
            meeting_id=mid, cluster_id=c.id, start_seconds=3.0, end_seconds=4.0, text="placeholder"
        )
        second = Segment(
            meeting_id=mid, cluster_id=c.id, start_seconds=3.0, end_seconds=4.0, text="beta"
        )
        s.add_all([first, second])
        s.flush()
        # Must be a REAL value change -- see test_attribute.py.
        first.text = "alpha"
        s.flush()
        first_id, second_id = first.id, second.id
    assert first_id < second_id
    # Dropping `Segment.id` from render_cited_transcript's order_by turns this red.
    assert ex.render_cited_transcript(mid) == (
        f"[{first_id}][SPEAKER_00] alpha\n[{second_id}][SPEAKER_00] beta"
    )
