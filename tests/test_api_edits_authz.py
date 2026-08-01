import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import (
    Account,
    AuditLogEntry,
    KeyTopic,
    Meeting,
    Organization,
    SpeakerCluster,
)


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member", password="pw") -> tuple[int, str]:
    email = f"{role}-{uuid.uuid4()}@x.com"
    with get_session() as s:
        a = Account(
            organization_id=org_id, email=email, role=role, password_hash=hash_password(password)
        )
        s.add(a)
        s.flush()
        return a.id, email


def _client_as(email: str, password: str = "pw") -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": password}).status_code == 200
    return c


def _meeting_with_topic(org_id, owner_id=None, visibility="organization"):
    with get_session() as s:
        m = Meeting(
            organization_id=org_id,
            title="t",
            status="published",
            owner_account_id=owner_id,
            visibility=visibility,
        )
        s.add(m)
        s.flush()
        t = KeyTopic(meeting_id=m.id, title="budget", citations=[1], provenance="inferred")
        c = SpeakerCluster(meeting_id=m.id, label="SPEAKER_00", spans=[])
        s.add_all([t, c])
        s.flush()
        return m.id, t.id, c.id


def test_editing_another_orgs_artifact_is_404():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_b, tid_b, _ = _meeting_with_topic(org_b)
    r = _client_as(email_a).patch(f"/meetings/{mid_b}/key_topics/{tid_b}", json={"title": "x"})
    assert r.status_code == 404


def test_auditor_cannot_edit_an_artifact():
    org_id = _org()
    _, auditor_email = _account(org_id, role="auditor")
    mid, tid, _ = _meeting_with_topic(org_id)
    r = _client_as(auditor_email).patch(f"/meetings/{mid}/key_topics/{tid}", json={"title": "x"})
    assert r.status_code == 403


def test_auditor_cannot_delete_an_artifact():
    org_id = _org()
    _, auditor_email = _account(org_id, role="auditor")
    mid, tid, _ = _meeting_with_topic(org_id)
    r = _client_as(auditor_email).delete(f"/meetings/{mid}/key_topics/{tid}")
    assert r.status_code == 403


def test_auditor_cannot_regenerate():
    org_id = _org()
    _, auditor_email = _account(org_id, role="auditor")
    mid, _, _ = _meeting_with_topic(org_id)
    r = _client_as(auditor_email).post(f"/meetings/{mid}/regenerate/key_topics")
    assert r.status_code == 403


def test_auditor_cannot_confirm_attribution():
    org_id = _org()
    _, auditor_email = _account(org_id, role="auditor")
    mid, _, cid = _meeting_with_topic(org_id)
    r = _client_as(auditor_email).patch(
        f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"}
    )
    assert r.status_code == 403


def test_a_member_editing_their_own_meeting_writes_an_audit_entry():
    org_id = _org()
    member_id, email = _account(org_id)
    mid, tid, _ = _meeting_with_topic(org_id, owner_id=member_id, visibility="private")
    r = _client_as(email).patch(f"/meetings/{mid}/key_topics/{tid}", json={"title": "renamed"})
    assert r.status_code == 200
    with get_session() as s:
        entry = (
            s.query(AuditLogEntry).filter_by(organization_id=org_id, action="artifact.edit").one()
        )
        assert entry.actor_account_id == member_id
        assert entry.target == f"meeting:{mid}:key_topics:{tid}"


def test_confirming_attribution_on_another_orgs_cluster_is_404():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_b, _, cid_b = _meeting_with_topic(org_b)
    r = _client_as(email_a).patch(
        f"/meetings/{mid_b}/clusters/{cid_b}", json={"participant_name": "Sarah"}
    )
    assert r.status_code == 404


def test_unauthenticated_edit_is_401():
    org_id = _org()
    mid, tid, _ = _meeting_with_topic(org_id)
    r = TestClient(app).patch(f"/meetings/{mid}/key_topics/{tid}", json={"title": "x"})
    assert r.status_code == 401


def test_unauthenticated_delete_is_401():
    org_id = _org()
    mid, tid, _ = _meeting_with_topic(org_id)
    r = TestClient(app).delete(f"/meetings/{mid}/key_topics/{tid}")
    assert r.status_code == 401


def test_unauthenticated_regenerate_is_401():
    org_id = _org()
    mid, _, _ = _meeting_with_topic(org_id)
    r = TestClient(app).post(f"/meetings/{mid}/regenerate/key_topics")
    assert r.status_code == 401


def test_unauthenticated_confirm_attribution_is_401():
    org_id = _org()
    mid, _, cid = _meeting_with_topic(org_id)
    r = TestClient(app).patch(f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"})
    assert r.status_code == 401


def test_deleting_another_orgs_artifact_is_404():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_b, tid_b, _ = _meeting_with_topic(org_b)
    r = _client_as(email_a).delete(f"/meetings/{mid_b}/key_topics/{tid_b}")
    assert r.status_code == 404


def test_regenerating_another_orgs_artifact_is_404():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_b, _, _ = _meeting_with_topic(org_b)
    r = _client_as(email_a).post(f"/meetings/{mid_b}/regenerate/key_topics")
    assert r.status_code == 404


def test_deleting_an_artifact_writes_an_audit_entry_that_identifies_the_meeting():
    org_id = _org()
    member_id, email = _account(org_id)
    mid, tid, _ = _meeting_with_topic(org_id, owner_id=member_id, visibility="private")
    r = _client_as(email).delete(f"/meetings/{mid}/key_topics/{tid}")
    assert r.status_code == 204
    with get_session() as s:
        entry = (
            s.query(AuditLogEntry).filter_by(organization_id=org_id, action="artifact.delete").one()
        )
        assert entry.actor_account_id == member_id
        # The row is gone by now — target is the ONLY place meeting_id
        # survives for this entry, so a deleted artifact's audit trail
        # still says which meeting it belonged to.
        assert entry.target == f"meeting:{mid}:key_topics:{tid}"


def test_regenerating_an_artifact_writes_an_audit_entry_that_identifies_the_meeting(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.edits.extract_key_topics", lambda meeting_id: None)
    org_id = _org()
    member_id, email = _account(org_id)
    mid, _, _ = _meeting_with_topic(org_id, owner_id=member_id, visibility="private")
    r = _client_as(email).post(f"/meetings/{mid}/regenerate/key_topics")
    assert r.status_code == 202
    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="artifact.regenerate")
            .one()
        )
        assert entry.actor_account_id == member_id
        # "someone regenerated key_topics" with no meeting is not an
        # answerable audit trail for the most destructive action here.
        assert entry.target == f"meeting:{mid}:key_topics"


def test_confirming_attribution_writes_an_audit_entry():
    org_id = _org()
    member_id, email = _account(org_id)
    mid, _, cid = _meeting_with_topic(org_id, owner_id=member_id, visibility="private")
    r = _client_as(email).patch(
        f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"}
    )
    assert r.status_code == 200
    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="cluster.attribute")
            .one()
        )
        assert entry.actor_account_id == member_id
        assert entry.target == f"meeting:{mid}:cluster:{cid}"
        assert "participant_name" not in entry.detail, (
            "audit detail must not carry the person's name, only an id reference"
        )
