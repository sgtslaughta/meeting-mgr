import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, AuditLogEntry, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member") -> tuple[str, int]:
    email = f"{role}-{uuid.uuid4()}@x.com"
    with get_session() as s:
        a = Account(
            organization_id=org_id, email=email, role=role, password_hash=hash_password("pw")
        )
        s.add(a)
        s.flush()
        return email, a.id


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def test_unauthenticated_cannot_trigger_a_purge():
    r = TestClient(app).post("/retention-policy/purge")
    assert r.status_code == 401


def test_member_cannot_trigger_a_purge():
    org_id = _org()
    email, _ = _account(org_id, role="member")
    r = _client_as(email).post("/retention-policy/purge")
    assert r.status_code == 403


def test_auditor_cannot_trigger_a_purge():
    org_id = _org()
    email, _ = _account(org_id, role="auditor")
    r = _client_as(email).post("/retention-policy/purge")
    assert r.status_code == 403


def test_admin_can_trigger_a_purge_and_it_is_enqueued(monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "meeting_mgr.pipeline.purge.purge_organization.delay",
        lambda org_id: enqueued.append(org_id),
    )

    org_id = _org()
    email, account_id = _account(org_id, role="admin")
    r = _client_as(email).post("/retention-policy/purge")
    assert r.status_code == 202
    assert enqueued == [org_id]


def test_admin_of_one_org_purges_only_their_own_org(monkeypatch):
    enqueued = []
    monkeypatch.setattr(
        "meeting_mgr.pipeline.purge.purge_organization.delay",
        lambda org_id: enqueued.append(org_id),
    )

    org_id = _org()
    other_org_id = _org()
    email, _ = _account(org_id, role="admin")
    _account(other_org_id, role="admin")

    r = _client_as(email).post("/retention-policy/purge")
    assert r.status_code == 202
    assert enqueued == [org_id]
    assert other_org_id not in enqueued


def test_trigger_dispatches_via_delay_not_inline(monkeypatch):
    """The dispatch must be through .delay() -- spy on the call itself,
    not a downstream effect, since task_always_eager makes .delay() run
    synchronously and would otherwise mask an inline call."""
    calls = []
    monkeypatch.setattr(
        "meeting_mgr.pipeline.purge.purge_organization.delay",
        lambda org_id: calls.append(org_id),
    )

    org_id = _org()
    email, _ = _account(org_id, role="admin")
    _client_as(email).post("/retention-policy/purge")

    assert calls == [org_id]


def test_trigger_records_who_triggered_it(monkeypatch):
    monkeypatch.setattr("meeting_mgr.pipeline.purge.purge_organization.delay", lambda org_id: None)

    org_id = _org()
    email, account_id = _account(org_id, role="admin")
    _client_as(email).post("/retention-policy/purge")

    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="retention.purge.triggered")
            .one()
        )
        assert entry.actor_account_id == account_id
        assert entry.target == f"organization:{org_id}"


def test_audit_entry_is_written_before_dispatch(monkeypatch):
    """The audit entry naming the acting account must exist even if the
    worker dispatch fails -- so the record of "who asked for this" survives
    a dead worker. Make .delay() raise and confirm the audit row is still
    there."""

    def _boom(org_id):
        raise RuntimeError("worker unreachable")

    monkeypatch.setattr("meeting_mgr.pipeline.purge.purge_organization.delay", _boom)

    org_id = _org()
    email, account_id = _account(org_id, role="admin")

    c = TestClient(app, raise_server_exceptions=False)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    r = c.post("/retention-policy/purge")
    assert r.status_code == 500

    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="retention.purge.triggered")
            .one()
        )
        assert entry.actor_account_id == account_id
