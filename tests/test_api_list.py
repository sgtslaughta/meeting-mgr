import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _account_and_client() -> TestClient:
    email = f"list-{uuid.uuid4()}@x.com"
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        s.add(Account(organization_id=org.id, email=email, password_hash=hash_password("pw")))
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200
    return c


def test_list_returns_meetings_newest_first(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = _account_and_client()
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
