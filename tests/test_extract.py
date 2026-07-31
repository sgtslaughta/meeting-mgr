from meeting_mgr.db import get_session
from meeting_mgr.models import ActionItem, KeyTopic, Segment, SpeakerCluster
from meeting_mgr.pipeline import extract as ex

def _seed(mid) -> int:
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c); s.flush()
        seg = Segment(meeting_id=mid, cluster_id=c.id, start_seconds=0.0,
                      end_seconds=1.0, text="I will ship the migration")
        s.add(seg); s.flush()
        return seg.id

def test_render_cited_transcript_includes_segment_ids(make_meeting):
    mid = make_meeting(b"RIFFfake"); sid = _seed(mid)
    assert ex.render_cited_transcript(mid) == f"[{sid}][SPEAKER_00] I will ship the migration"

def test_extract_key_topics_stores_citations(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake"); sid = _seed(mid)
    monkeypatch.setattr(ex, "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"topics": [{"title": "migration", "citations": [sid]}]}))
    ex.extract_key_topics(mid)
    with get_session() as s:
        t = s.query(KeyTopic).filter_by(meeting_id=mid).one()
        assert t.title == "migration"
        assert t.citations == [sid]
        assert t.provenance == "inferred"

def test_extract_action_items_links_participant(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake"); sid = _seed(mid)
    monkeypatch.setattr(ex, "structured_chat",
        lambda prompt, schema, **kw: schema.model_validate(
            {"action_items": [{"text": "ship the migration",
                               "participant_name": "Sarah", "citations": [sid]}]}))
    ex.extract_action_items(mid)
    with get_session() as s:
        item = s.query(ActionItem).filter_by(meeting_id=mid).one()
        assert item.participant_id is not None
        assert item.citations == [sid]
