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


def test_a_losing_race_on_start_returns_200_not_500():
    # Simulates two concurrent starts for the same (credential, platform_meeting_id):
    # the check-then-insert in start_session has no lock, so the loser must
    # hit uq_bot_session_credential_platform's IntegrityError at flush and
    # come back as an idempotent 200 replay, not an uncaught 500.
    #
    # A before_flush hook on the org-scoped sessionmaker fires right as our
    # request is about to INSERT its BotSession row -- after its own
    # check-query already ran and found nothing. At that instant a
    # completely separate connection inserts and commits the "winning" row
    # for the same (credential, platform_meeting_id), so our request's own
    # flush is the one that collides with the now-committed unique
    # constraint -- reproducing the race without real concurrency.
    from sqlalchemy import event

    from meeting_mgr.db import OrgSessionLocal

    org_id, account_id = _org_account()
    token = _token(org_id, account_id)
    credential_id = int(token.split(".")[0])
    winner: dict = {}
    fired = {"done": False}

    def _before_flush(session, flush_context, instances):
        if fired["done"]:
            return
        for obj in session.new:
            if isinstance(obj, BotSession) and obj.platform_meeting_id == "race-id":
                fired["done"] = True
                with get_session() as s2:
                    m2 = Meeting(
                        organization_id=org_id,
                        owner_account_id=account_id,
                        title="winner",
                        status="capturing",
                    )
                    s2.add(m2)
                    s2.flush()
                    w = BotSession(
                        organization_id=org_id,
                        bot_credential_id=credential_id,
                        meeting_id=m2.id,
                        platform_meeting_id="race-id",
                    )
                    s2.add(w)
                    s2.flush()
                    winner["meeting_id"] = m2.id
                break

    event.listen(OrgSessionLocal, "before_flush", _before_flush)
    try:
        c = TestClient(app)
        r = c.post(
            "/bot/sessions",
            json={"platform_meeting_id": "race-id", "title": "loser"},
            headers={"authorization": f"Bearer {token}"},
        )
    finally:
        event.remove(OrgSessionLocal, "before_flush", _before_flush)

    assert fired["done"]
    assert r.status_code == 200
    assert r.json()["meeting_id"] == winner["meeting_id"]


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
