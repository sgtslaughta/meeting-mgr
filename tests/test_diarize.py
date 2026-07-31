from meeting_mgr.db import get_session
from meeting_mgr.models import SpeakerCluster
from meeting_mgr.pipeline.diarize import diarize


def test_diarize_persists_clusters(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        from meeting_mgr.models import Recording
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = f"raw/{mid}/a.wav"
    monkeypatch.setattr(
        "meeting_mgr.pipeline.diarize._call_diarizer",
        lambda audio: {"clusters": [
            {"label": "SPEAKER_00", "embedding": [0.1, 0.2],
             "spans": [{"start": 0.0, "end": 2.0}]},
            {"label": "SPEAKER_01", "embedding": [0.3, 0.4],
             "spans": [{"start": 2.0, "end": 4.0}]},
        ]},
    )
    diarize(mid)
    with get_session() as s:
        clusters = s.query(SpeakerCluster).filter_by(meeting_id=mid).all()
        assert {c.label for c in clusters} == {"SPEAKER_00", "SPEAKER_01"}
        assert clusters[0].spans == [{"start": 0.0, "end": 2.0}]
