from fastapi.testclient import TestClient
from meeting_mgr.api.main import app
from meeting_mgr.storage import get_object

def test_upload_creates_meeting_and_stores_recording(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    r = c.post("/meetings", data={"title": "standup"},
               files={"file": ("a.m4a", b"AUDIO", "audio/mp4")})
    assert r.status_code == 201
    mid = r.json()["meeting_id"]
    assert get_object(f"raw/{mid}/a.m4a") == b"AUDIO"
