from meeting_mgr.db import get_session
from meeting_mgr.models import Recording, Segment
from meeting_mgr.pipeline.transcribe import transcribe

def test_transcribe_writes_segments(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = f"raw/{mid}/a.wav"
    monkeypatch.setattr(
        "meeting_mgr.pipeline.transcribe.transcribe_audio",
        lambda audio: [{"start": 0.0, "end": 2.0, "text": "hello"},
                       {"start": 2.0, "end": 4.0, "text": "world"}],
    )
    transcribe(mid)
    with get_session() as s:
        segs = s.query(Segment).filter_by(meeting_id=mid).order_by(Segment.start_seconds).all()
        assert [x.text for x in segs] == ["hello", "world"]
        assert all(x.cluster_id is None for x in segs)
