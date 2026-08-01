import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.audit import record_audit
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member") -> tuple[int, str]:
    email = f"{role}-{uuid.uuid4()}@x.com"
    with get_session() as s:
        a = Account(
            organization_id=org_id, email=email, role=role, password_hash=hash_password("pw")
        )
        s.add(a)
        s.flush()
        return a.id, email


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def test_unauthenticated_cannot_read_the_audit_log():
    r = TestClient(app).get("/audit-log")
    assert r.status_code == 401


def test_member_cannot_read_the_audit_log():
    org_id = _org()
    _, email = _account(org_id, role="member")
    r = _client_as(email).get("/audit-log")
    assert r.status_code == 403


def test_auditor_can_read_the_audit_log():
    org_id = _org()
    actor_id, _ = _account(org_id)
    with get_session() as s:
        record_audit(
            s,
            organization_id=org_id,
            actor_account_id=actor_id,
            action="artifact.edit",
            target="key_topics:1",
        )
    _, auditor_email = _account(org_id, role="auditor")
    r = _client_as(auditor_email).get("/audit-log")
    assert r.status_code == 200
    assert any(e["action"] == "artifact.edit" for e in r.json())


def test_audit_log_is_scoped_to_the_callers_organization():
    org_a, org_b = _org(), _org()
    actor_b, _ = _account(org_b)
    with get_session() as s:
        record_audit(
            s,
            organization_id=org_b,
            actor_account_id=actor_b,
            action="artifact.delete",
            target="minutes:9",
        )
    _, auditor_a_email = _account(org_a, role="auditor")
    body = _client_as(auditor_a_email).get("/audit-log").json()
    assert body == []
