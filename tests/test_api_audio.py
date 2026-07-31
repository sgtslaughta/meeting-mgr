from fastapi.testclient import TestClient
from meeting_mgr.api.main import app
from meeting_mgr.db import get_session
from meeting_mgr.models import Recording
from meeting_mgr.storage import ensure_bucket, put_object

def _published_with_audio(monkeypatch, data: bytes) -> int:
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    mid = c.post("/meetings", data={"title": "t"},
                 files={"file": ("a.wav", b"raw", "audio/wav")}).json()["meeting_id"]
    ensure_bucket()
    key = f"normalized/{mid}.wav"
    put_object(key, data)
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = key
    return mid

def test_full_request_returns_whole_object(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio")
    assert r.status_code == 200
    assert r.content == b"0123456789"
    assert r.headers["accept-ranges"] == "bytes"

def test_range_request_returns_partial_content(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio",
                            headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"

def test_open_ended_range_runs_to_the_end(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio",
                            headers={"Range": "bytes=7-"})
    assert r.status_code == 206
    assert r.content == b"789"
    assert r.headers["content-range"] == "bytes 7-9/10"

def test_missing_audio_is_404(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    mid = c.post("/meetings", data={"title": "t"},
                 files={"file": ("a.wav", b"raw", "audio/wav")}).json()["meeting_id"]
    assert c.get(f"/meetings/{mid}/audio").status_code == 404
