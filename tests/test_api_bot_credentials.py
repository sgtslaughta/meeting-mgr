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


def test_unauthenticated_request_is_refused():
    org_id = _org()
    _account(org_id, role="admin")
    r = TestClient(app).get("/bot-credentials")
    assert r.status_code == 401


def test_member_cannot_create_a_bot_credential():
    org_id = _org()
    email, account_id = _account(org_id, role="member")
    r = _client_as(email).post(
        "/bot-credentials", json={"label": "a", "owner_account_id": account_id}
    )
    assert r.status_code == 403


def test_member_cannot_list_bot_credentials():
    org_id = _org()
    email, _ = _account(org_id, role="member")
    r = _client_as(email).get("/bot-credentials")
    assert r.status_code == 403


def test_admin_creates_a_credential_and_the_token_is_returned_once():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    r = c.post("/bot-credentials", json={"label": "zoom-bot-1", "owner_account_id": admin_id})
    assert r.status_code == 201
    body = r.json()
    assert body["label"] == "zoom-bot-1"
    assert "." in body["token"]

    listed = c.get("/bot-credentials").json()
    assert listed[0]["label"] == "zoom-bot-1"
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]


def test_owner_account_must_belong_to_the_same_organization():
    org_a, org_b = _org(), _org()
    admin_email, _ = _account(org_a, role="admin")
    _, other_org_account_id = _account(org_b, role="member")
    r = _client_as(admin_email).post(
        "/bot-credentials", json={"label": "a", "owner_account_id": other_org_account_id}
    )
    assert r.status_code == 422


def test_owner_account_that_does_not_exist_is_rejected():
    org_id = _org()
    admin_email, _ = _account(org_id, role="admin")
    r = _client_as(admin_email).post(
        "/bot-credentials", json={"label": "a", "owner_account_id": 9_999_999}
    )
    assert r.status_code == 422


def test_admin_revokes_a_credential():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    cred_id = c.post("/bot-credentials", json={"label": "a", "owner_account_id": admin_id}).json()[
        "id"
    ]

    r = c.post(f"/bot-credentials/{cred_id}/revoke")
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None


def test_revoking_an_already_revoked_credential_is_idempotent():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    cred_id = c.post("/bot-credentials", json={"label": "a", "owner_account_id": admin_id}).json()[
        "id"
    ]

    first = c.post(f"/bot-credentials/{cred_id}/revoke")
    second = c.post(f"/bot-credentials/{cred_id}/revoke")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["revoked_at"] is not None


def test_revoke_of_a_credential_in_another_organization_is_not_found():
    org_a, org_b = _org(), _org()
    admin_a_email, admin_a_id = _account(org_a, role="admin")
    admin_b_email, admin_b_id = _account(org_b, role="admin")
    cred_id = (
        _client_as(admin_a_email)
        .post("/bot-credentials", json={"label": "a", "owner_account_id": admin_a_id})
        .json()["id"]
    )

    r = _client_as(admin_b_email).post(f"/bot-credentials/{cred_id}/revoke")
    assert r.status_code == 404


def test_a_second_organizations_admin_does_not_see_the_first_organizations_credential():
    org_a, org_b = _org(), _org()
    admin_a_email, admin_a_id = _account(org_a, role="admin")
    admin_b_email, _ = _account(org_b, role="admin")
    _client_as(admin_a_email).post(
        "/bot-credentials", json={"label": "a", "owner_account_id": admin_a_id}
    )
    listed = _client_as(admin_b_email).get("/bot-credentials").json()
    assert listed == []
