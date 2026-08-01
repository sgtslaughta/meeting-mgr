import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization, WatchFolder


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
    r = TestClient(app).get("/watch-folders")
    assert r.status_code == 401


def test_member_cannot_read_watch_folders():
    org_id = _org()
    email, _ = _account(org_id, role="member")
    r = _client_as(email).get("/watch-folders")
    assert r.status_code == 403


def test_member_cannot_write_watch_folders():
    org_id = _org()
    email, account_id = _account(org_id, role="member")
    r = _client_as(email).put(
        "/watch-folders", json={"root_path": "/data/a", "owner_account_id": account_id}
    )
    assert r.status_code == 403


def test_auditor_cannot_read_watch_folders():
    org_id = _org()
    email, _ = _account(org_id, role="auditor")
    r = _client_as(email).get("/watch-folders")
    assert r.status_code == 403


def test_auditor_cannot_write_watch_folders():
    org_id = _org()
    email, account_id = _account(org_id, role="auditor")
    r = _client_as(email).put(
        "/watch-folders", json={"root_path": "/data/a", "owner_account_id": account_id}
    )
    assert r.status_code == 403


def test_admin_can_register_a_watch_folder():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    r = c.put("/watch-folders", json={"root_path": "/data/a", "owner_account_id": admin_id})
    assert r.status_code == 200
    body = r.json()
    assert body["root_path"] == "/data/a"
    assert body["owner_account_id"] == admin_id
    assert body["enabled"] is True
    listed = c.get("/watch-folders").json()
    assert [w["root_path"] for w in listed] == ["/data/a"]


def test_relative_root_path_is_rejected():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    r = _client_as(admin_email).put(
        "/watch-folders", json={"root_path": "data/a", "owner_account_id": admin_id}
    )
    assert r.status_code == 422


def test_owner_account_must_belong_to_the_same_organization():
    org_a, org_b = _org(), _org()
    admin_email, _ = _account(org_a, role="admin")
    _, other_org_account_id = _account(org_b, role="member")
    r = _client_as(admin_email).put(
        "/watch-folders", json={"root_path": "/data/a", "owner_account_id": other_org_account_id}
    )
    assert r.status_code == 422


def test_a_fresh_folder_is_not_reported_stalled():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    c = _client_as(admin_email)
    c.put("/watch-folders", json={"root_path": "/data/a", "owner_account_id": admin_id})
    body = c.get("/watch-folders").json()
    assert body[0]["stalled"] is False


def test_a_folder_whose_last_scan_is_long_past_is_reported_stalled():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    with get_session() as s:
        s.add(
            WatchFolder(
                organization_id=org_id,
                owner_account_id=admin_id,
                root_path="/data/a",
                last_scan_at=datetime.utcnow() - timedelta(hours=6),
            )
        )
    body = _client_as(admin_email).get("/watch-folders").json()
    assert body[0]["stalled"] is True


def test_a_disabled_folder_is_never_reported_stalled():
    org_id = _org()
    admin_email, admin_id = _account(org_id, role="admin")
    with get_session() as s:
        s.add(
            WatchFolder(
                organization_id=org_id,
                owner_account_id=admin_id,
                root_path="/data/a",
                enabled=False,
                last_scan_at=datetime.utcnow() - timedelta(hours=6),
            )
        )
    body = _client_as(admin_email).get("/watch-folders").json()
    assert body[0]["stalled"] is False


def test_a_second_organizations_admin_does_not_see_the_first_organizations_folder():
    org_a, org_b = _org(), _org()
    admin_a_email, admin_a_id = _account(org_a, role="admin")
    admin_b_email, _ = _account(org_b, role="admin")
    _client_as(admin_a_email).put(
        "/watch-folders", json={"root_path": "/data/a", "owner_account_id": admin_a_id}
    )
    listed = _client_as(admin_b_email).get("/watch-folders").json()
    assert listed == []
