import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.bot_credentials import create_bot_credential, revoke_bot_credential
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, BotSession, Meeting, Organization


def _org_account() -> tuple[int, int]:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        a = Account(organization_id=o.id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return o.id, a.id


def _token(org_id, account_id, label="bot") -> str:
    with get_session() as s:
        _, token = create_bot_credential(s, org_id, label=label, owner_account_id=account_id)
        return token


def test_missing_token_is_rejected():
    c = TestClient(app)
    r = c.post("/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"})
    assert r.status_code == 401


def test_garbage_token_is_rejected():
    c = TestClient(app)
    r = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "z-1", "title": "t"},
        headers={"authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


def test_revoked_token_is_rejected():
    org_id, account_id = _org_account()
    token = _token(org_id, account_id)
    with get_session() as s:
        revoke_bot_credential(s, org_id, int(token.split(".")[0]))
    c = TestClient(app)
    r = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "z-1", "title": "t"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_a_valid_token_starts_a_session_and_creates_a_capturing_meeting():
    org_id, account_id = _org_account()
    token = _token(org_id, account_id)
    c = TestClient(app)
    r = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "z-1", "title": "Standup"},
        headers={"authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201
    body = r.json()
    with get_session() as s:
        m = s.get(Meeting, body["meeting_id"])
        assert m.organization_id == org_id
        assert m.owner_account_id == account_id
        assert m.status == "capturing"
        assert m.title == "Standup"


def test_a_retried_start_with_the_same_platform_meeting_id_returns_the_same_session():
    org_id, account_id = _org_account()
    token = _token(org_id, account_id)
    c = TestClient(app)
    headers = {"authorization": f"Bearer {token}"}
    r1 = c.post("/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"}, headers=headers)
    r2 = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "z-1", "title": "t (retry)"},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["meeting_id"] == r1.json()["meeting_id"]
    with get_session() as s:
        assert (
            s.query(BotSession)
            .filter_by(bot_credential_id=int(token.split(".")[0]), platform_meeting_id="z-1")
            .count()
            == 1
        )


def test_two_different_credentials_can_reuse_the_same_platform_meeting_id_independently():
    org_id, account_id = _org_account()
    token_a = _token(org_id, account_id, label="bot-a")
    token_b = _token(org_id, account_id, label="bot-b")
    c = TestClient(app)
    r_a = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "shared-id", "title": "t"},
        headers={"authorization": f"Bearer {token_a}"},
    )
    r_b = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "shared-id", "title": "t"},
        headers={"authorization": f"Bearer {token_b}"},
    )
    assert r_a.json()["meeting_id"] != r_b.json()["meeting_id"]


def test_a_session_is_scoped_to_the_credentials_own_organization_and_not_another():
    org_a, account_a = _org_account()
    org_b, account_b = _org_account()
    token_a = _token(org_a, account_a)
    token_b = _token(org_b, account_b)
    c = TestClient(app)
    r_a = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "distinct-id", "title": "t"},
        headers={"authorization": f"Bearer {token_a}"},
    )
    r_b = c.post(
        "/bot/sessions",
        json={"platform_meeting_id": "distinct-id", "title": "t"},
        headers={"authorization": f"Bearer {token_b}"},
    )
    assert r_a.json()["meeting_id"] != r_b.json()["meeting_id"]
    with get_session() as s:
        m_a = s.get(Meeting, r_a.json()["meeting_id"])
        m_b = s.get(Meeting, r_b.json()["meeting_id"])
        assert m_a.organization_id == org_a
        assert m_b.organization_id == org_b
