import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


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


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def test_member_cannot_read_retention_policy():
    org_id = _org()
    email = _account(org_id, role="member")
    r = _client_as(email).get("/retention-policy")
    assert r.status_code == 403


def test_auditor_cannot_read_retention_policy():
    org_id = _org()
    email = _account(org_id, role="auditor")
    r = _client_as(email).get("/retention-policy")
    assert r.status_code == 403


def test_unauthenticated_request_is_refused():
    org_id = _org()
    _account(org_id, role="admin")
    r = TestClient(app).get("/retention-policy")
    assert r.status_code == 401


def test_admin_gets_keep_forever_default():
    org_id = _org()
    email = _account(org_id, role="admin")
    r = _client_as(email).get("/retention-policy")
    assert r.status_code == 200
    assert r.json() == {"audio_retention_days": None, "meeting_retention_days": None}


def test_member_cannot_write_retention_policy():
    org_id = _org()
    email = _account(org_id, role="member")
    r = _client_as(email).put("/retention-policy", json={"audio_retention_days": 30})
    assert r.status_code == 403


def test_auditor_cannot_write_retention_policy():
    org_id = _org()
    email = _account(org_id, role="auditor")
    r = _client_as(email).put("/retention-policy", json={"audio_retention_days": 30})
    assert r.status_code == 403


def test_admin_can_set_and_read_back_a_policy():
    org_id = _org()
    email = _account(org_id, role="admin")
    c = _client_as(email)
    r = c.put("/retention-policy", json={"audio_retention_days": 30, "meeting_retention_days": 90})
    assert r.status_code == 200
    assert r.json() == {"audio_retention_days": 30, "meeting_retention_days": 90}
    assert c.get("/retention-policy").json() == {
        "audio_retention_days": 30,
        "meeting_retention_days": 90,
    }


def test_admin_can_set_purge_immediately_and_read_it_back_as_zero():
    org_id = _org()
    email = _account(org_id, role="admin")
    c = _client_as(email)
    r = c.put("/retention-policy", json={"audio_retention_days": 0, "meeting_retention_days": 0})
    assert r.status_code == 200
    assert r.json() == {"audio_retention_days": 0, "meeting_retention_days": 0}
    assert c.get("/retention-policy").json() == {
        "audio_retention_days": 0,
        "meeting_retention_days": 0,
    }


def test_admin_can_reset_to_null_keep_forever():
    org_id = _org()
    email = _account(org_id, role="admin")
    c = _client_as(email)
    c.put("/retention-policy", json={"audio_retention_days": 30, "meeting_retention_days": 90})
    r = c.put("/retention-policy", json={})
    assert r.status_code == 200
    assert r.json() == {"audio_retention_days": None, "meeting_retention_days": None}
    assert c.get("/retention-policy").json() == {
        "audio_retention_days": None,
        "meeting_retention_days": None,
    }


def test_audio_retention_cannot_exceed_meeting_retention():
    org_id = _org()
    email = _account(org_id, role="admin")
    r = _client_as(email).put(
        "/retention-policy", json={"audio_retention_days": 100, "meeting_retention_days": 30}
    )
    assert r.status_code == 422


def test_negative_retention_days_is_rejected():
    org_id = _org()
    email = _account(org_id, role="admin")
    r = _client_as(email).put("/retention-policy", json={"audio_retention_days": -5})
    assert r.status_code == 422


def test_a_second_organizations_admin_does_not_see_the_first_organizations_policy():
    org_a, org_b = _org(), _org()
    admin_a = _account(org_a, role="admin")
    admin_b = _account(org_b, role="admin")
    _client_as(admin_a).put("/retention-policy", json={"meeting_retention_days": 45})
    r = _client_as(admin_b).get("/retention-policy")
    assert r.json() == {"audio_retention_days": None, "meeting_retention_days": None}


def test_a_second_organizations_admin_cannot_write_the_first_organizations_policy():
    org_a, org_b = _org(), _org()
    admin_a = _account(org_a, role="admin")
    admin_b = _account(org_b, role="admin")
    _client_as(admin_a).put("/retention-policy", json={"meeting_retention_days": 45})
    _client_as(admin_b).put("/retention-policy", json={"meeting_retention_days": 999})
    r = _client_as(admin_a).get("/retention-policy")
    assert r.json() == {"audio_retention_days": None, "meeting_retention_days": 45}
