import io
import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.bot_credentials import create_bot_credential
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, AuditLogEntry, Meeting, Organization, Recording
from meeting_mgr.storage import ensure_bucket, get_object


def _org_account() -> tuple[int, int]:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        a = Account(organization_id=o.id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return o.id, a.id


def _client_and_session(monkeypatch, label="bot"):
    import meeting_mgr.api.bot as bot_module

    monkeypatch.setattr(bot_module, "run_pipeline", lambda meeting_id: None)
    ensure_bucket()
    org_id, account_id = _org_account()
    with get_session() as s:
        _, token = create_bot_credential(s, org_id, label=label, owner_account_id=account_id)
    c = TestClient(app)
    headers = {"authorization": f"Bearer {token}"}
    r = c.post("/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"}, headers=headers)
    body = r.json()
    return c, headers, body["session_id"], body["meeting_id"]


def test_finish_with_chunks_writes_a_manifest_and_enqueues_the_pipeline(monkeypatch):
    c, headers, session_id, meeting_id = _client_and_session(monkeypatch)
    c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )

    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.status == "pending"
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        assert rec.raw_key.startswith("manifest:")


def test_finish_with_zero_chunks_marks_the_meeting_failed_not_pending(monkeypatch):
    c, headers, session_id, meeting_id = _client_and_session(monkeypatch)

    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.status == "failed"
        assert m.failed_stage == "bot_ingest"
        assert s.query(Recording).filter_by(meeting_id=meeting_id).count() == 0
        assert (
            s.query(AuditLogEntry)
            .filter_by(organization_id=m.organization_id, action="meeting.bot_ingest.empty")
            .count()
            == 1
        )


def test_finish_twice_the_second_call_is_a_conflict(monkeypatch):
    c, headers, session_id, meeting_id = _client_and_session(monkeypatch)
    c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    c.post(f"/bot/sessions/{session_id}/finish", headers=headers)

    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers)
    assert r.status_code == 409


def test_finish_orders_more_than_ten_chunks_numerically_not_lexically(monkeypatch):
    # "10" sorts before "9" lexically -- with fewer than ten chunks the two
    # orders happen to agree, so this must cross ten to be a real test of
    # the parsed-int sort path.
    c, headers, session_id, meeting_id = _client_and_session(monkeypatch)
    for seq in range(11):
        c.put(
            f"/bot/sessions/{session_id}/chunks/{seq}",
            headers=headers,
            files={"chunk": ("c.bin", io.BytesIO(str(seq).encode()), "application/octet-stream")},
        )

    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers)
    assert r.status_code == 200
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        manifest_key = rec.raw_key.removeprefix("manifest:")
    import json

    manifest = json.loads(get_object(manifest_key))
    assert manifest == [f"raw/{meeting_id}/bot-chunks/{seq:06d}.chunk" for seq in range(11)]


def test_a_bot_cannot_finish_another_organizations_session(monkeypatch):
    c1, headers1, session_id, _ = _client_and_session(monkeypatch, label="bot-a")
    _, headers2, _, _ = _client_and_session(monkeypatch, label="bot-b")

    r = c1.post(f"/bot/sessions/{session_id}/finish", headers=headers2)
    assert r.status_code == 404
