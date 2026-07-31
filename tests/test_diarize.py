from meeting_mgr.db import get_session
from meeting_mgr.models import Recording, SpeakerCluster
from meeting_mgr.pipeline.diarize import diarize


def test_diarize_persists_clusters(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = f"raw/{mid}/a.wav"
    monkeypatch.setattr(
        "meeting_mgr.pipeline.diarize._call_diarizer",
        lambda fileobj: {
            "clusters": [
                {
                    "label": "SPEAKER_00",
                    "embedding": [0.1, 0.2],
                    "spans": [{"start": 0.0, "end": 2.0}],
                },
                {
                    "label": "SPEAKER_01",
                    "embedding": [0.3, 0.4],
                    "spans": [{"start": 2.0, "end": 4.0}],
                },
            ]
        },
    )
    diarize(mid)
    with get_session() as s:
        clusters = s.query(SpeakerCluster).filter_by(meeting_id=mid).all()
        assert {c.label for c in clusters} == {"SPEAKER_00", "SPEAKER_01"}
        assert clusters[0].spans == [{"start": 0.0, "end": 2.0}]


def test_diarize_streams_audio_to_the_service(monkeypatch, make_meeting):
    from meeting_mgr.pipeline import diarize as mod

    assert not hasattr(mod, "get_object"), "diarize must not import get_object"

    seen = {}

    def fake_call(fileobj):
        seen["read"] = fileobj.read()
        seen["is_file"] = hasattr(fileobj, "read") and not isinstance(fileobj, bytes)
        return {"clusters": []}

    monkeypatch.setattr(mod, "_call_diarizer", fake_call)

    mid = make_meeting(b"WAVBYTES")
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = f"raw/{mid}/a.wav"
    mod.diarize(mid)

    assert seen["is_file"], "_call_diarizer must receive a file object, not bytes"
    assert seen["read"] == b"WAVBYTES"
