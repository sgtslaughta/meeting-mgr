import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member") -> str:
    email = f"{role}-{uuid.uuid4()}@x.com"
    with get_session() as s:
        s.add(
            Account(
                organization_id=org_id,
                email=email,
                role=role,
                password_hash=hash_password("pw"),
            )
        )
    return email


def _admin(org_id) -> str:
    return _account(org_id, role="admin")


def _member(org_id) -> str:
    return _account(org_id, role="member")


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def _old_meeting(org_id: int, title: str = "ancient") -> int:
    with get_session() as s:
        m = Meeting(
            organization_id=org_id,
            title=title,
            created_at=datetime.utcnow() - timedelta(days=400),
        )
        s.add(m)
        s.flush()
        return m.id


def test_member_cannot_preview():
    org_id = _org()
    email = _member(org_id)
    r = _client_as(email).get("/retention-policy/preview")
    assert r.status_code == 403


def test_auditor_cannot_preview():
    org_id = _org()
    email = _account(org_id, role="auditor")
    r = _client_as(email).get("/retention-policy/preview")
    assert r.status_code == 403


def test_unauthenticated_cannot_preview():
    org_id = _org()
    _admin(org_id)
    r = TestClient(app).get("/retention-policy/preview")
    assert r.status_code == 401


def test_preview_reflects_a_configured_policy():
    org_id = _org()
    meeting_id = _old_meeting(org_id)
    admin_email = _admin(org_id)
    c = _client_as(admin_email)
    c.put("/retention-policy", json={"meeting_retention_days": 30})
    r = c.get("/retention-policy/preview")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["meeting_id"] == meeting_id
    assert body[0]["kind"] == "full"


def test_preview_is_empty_with_no_policy_set():
    org_id = _org()
    _old_meeting(org_id)
    admin_email = _admin(org_id)
    r = _client_as(admin_email).get("/retention-policy/preview")
    assert r.json() == []


def test_preview_never_deletes_anything():
    org_id = _org()
    meeting_id = _old_meeting(org_id)
    admin_email = _admin(org_id)
    c = _client_as(admin_email)
    c.put("/retention-policy", json={"meeting_retention_days": 30})
    c.get("/retention-policy/preview")
    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None, "GET must never mutate state"


def test_a_second_organizations_admin_cannot_see_the_first_organizations_candidates():
    org_a, org_b = _org(), _org()
    _old_meeting(org_a)
    admin_a = _admin(org_a)
    admin_b = _admin(org_b)
    _client_as(admin_a).put("/retention-policy", json={"meeting_retention_days": 30})
    r = _client_as(admin_b).get("/retention-policy/preview")
    assert r.status_code == 200
    assert r.json() == []


def test_preview_returns_the_whole_backlog_not_one_page():
    org_id = _org()
    for i in range(501):
        _old_meeting(org_id, title=f"ancient-{i}")
    admin_email = _admin(org_id)
    c = _client_as(admin_email)
    c.put("/retention-policy", json={"meeting_retention_days": 30})
    r = c.get("/retention-policy/preview")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 501


def test_preview_reports_provenance_counts():
    org_id = _org()
    meeting_id = _old_meeting(org_id)
    admin_email = _admin(org_id)
    c = _client_as(admin_email)
    c.put("/retention-policy", json={"meeting_retention_days": 30})
    r = c.get("/retention-policy/preview")
    body = r.json()
    assert body[0]["meeting_id"] == meeting_id
    assert "provenance_counts" in body[0]
    assert isinstance(body[0]["provenance_counts"], dict)
