from fastapi.testclient import TestClient

from meeting_mgr.api.main import app


def test_list_returns_meetings_newest_first(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    first = c.post(
        "/meetings", data={"title": "older"}, files={"file": ("a.m4a", b"A", "audio/mp4")}
    ).json()["meeting_id"]
    second = c.post(
        "/meetings", data={"title": "newer"}, files={"file": ("b.m4a", b"B", "audio/mp4")}
    ).json()["meeting_id"]

    body = c.get("/meetings").json()
    ids = [m["id"] for m in body]
    assert ids.index(second) < ids.index(first), "newest first"
    row = next(m for m in body if m["id"] == second)
    assert set(row) == {"id", "title", "status", "current_stage", "failed_stage", "created_at"}
