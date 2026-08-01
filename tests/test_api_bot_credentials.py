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


def test_cross_org_owner_lookup_returns_none_under_rls_is_the_active_guard():
    """Isolates which half of write_bot_credential's `owner is None or
    owner.organization_id != account.organization_id` check produces the
    422 in test_owner_account_must_belong_to_the_same_organization: under
    get_org_session (what the endpoint actually uses), s.get(Account,
    other_org_account_id) already returns None by itself -- RLS is what
    "owner is None" is catching, not the comparison.

    Kill: dropping tenant_isolation RLS from the `account` table (or
    swapping get_org_session for a non-RLS session here) turns this red --
    the lookup would then return the row instead of None.
    """
    from meeting_mgr.db import get_org_session

    org_a, org_b = _org(), _org()
    _, other_org_account_id = _account(org_b, role="member")
    with get_org_session(org_a) as s:
        assert s.get(Account, other_org_account_id) is None


def test_owner_organization_mismatch_comparison_is_correct_if_ever_reached():
    """The `owner.organization_id != account.organization_id` half is
    unreachable in production today (previous test) because RLS already
    returns None first. This does NOT exercise the live endpoint -- it
    establishes that the comparison itself is correct and would still catch
    a cross-org owner if this endpoint were ever moved off get_org_session
    onto a non-RLS session (get_session()), which is exactly the scenario
    the comment at api/bot_credentials.py documents as the reason it stays.

    Kill: inverting the comparison (== instead of !=) turns this red.
    """
    org_a, org_b = _org(), _org()
    _, other_org_account_id = _account(org_b, role="member")
    with get_session() as s:  # RLS-exempt, same as a non-RLS session would be
        owner = s.get(Account, other_org_account_id)
        assert owner is not None
        assert owner.organization_id != org_a


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


def test_a_duplicate_label_is_rejected_with_409_not_a_500():
    """uq_bot_credential_org_label turns a repeat label into an
    IntegrityError. Without a handler that surfaces as an uncaught 500;
    the endpoint must return a 409 the caller can act on.

    The third request is not testing session reuse -- it is a fresh request
    with its own session. It pins that a rejected duplicate leaves no
    half-created row behind that would block the next legitimate label.
    """
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    first = c.post("/bot-credentials", json={"label": "dupe", "owner_account_id": admin_id})
    assert first.status_code == 201

    second = c.post("/bot-credentials", json={"label": "dupe", "owner_account_id": admin_id})
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]

    # A different label still works afterwards -- proves the failed insert
    # did not poison the session or leave the endpoint wedged.
    third = c.post("/bot-credentials", json={"label": "not-dupe", "owner_account_id": admin_id})
    assert third.status_code == 201
