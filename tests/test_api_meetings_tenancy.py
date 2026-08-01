from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization


def _org() -> int:
    with get_session() as s:
        import uuid

        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member", password="pw") -> tuple[int, str]:
    import uuid

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
    r = c.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    return c


def _meeting(org_id, owner_id=None, visibility="organization") -> int:
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
        return m.id


def test_list_meetings_requires_authentication():
    r = TestClient(app).get("/meetings")
    assert r.status_code == 401


def test_list_meetings_excludes_other_organizations():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_a = _meeting(org_a)
    _meeting(org_b)  # must never appear for org_a's caller

    c = _client_as(email_a)
    body = c.get("/meetings").json()
    ids = [m["id"] for m in body]
    assert mid_a in ids
    assert len(ids) == 1, "a meeting in a different Organization leaked into the list"


def test_list_meetings_paginates():
    org_id = _org()
    _, email = _account(org_id)
    for _ in range(3):
        _meeting(org_id)
    c = _client_as(email)
    page = c.get("/meetings?limit=2&offset=0").json()
    assert len(page) == 2


def test_reading_a_meeting_in_another_organization_is_404():
    org_a, org_b = _org(), _org()
    _, email_a = _account(org_a)
    mid_b = _meeting(org_b)
    c = _client_as(email_a)
    assert c.get(f"/meetings/{mid_b}").status_code == 404


def test_reading_a_private_meeting_you_do_not_own_is_404():
    org_id = _org()
    owner_id, _ = _account(org_id)
    _, other_email = _account(org_id)
    mid = _meeting(org_id, owner_id=owner_id, visibility="private")
    c = _client_as(other_email)
    assert c.get(f"/meetings/{mid}").status_code == 404


def test_create_meeting_requires_authentication():
    r = TestClient(app).post(
        "/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")}
    )
    assert r.status_code == 401


def test_create_meeting_ignores_any_client_supplied_organization(monkeypatch):
    # There is no organization_id field in the request today, but this pins
    # the contract so one is never accidentally honoured if the form grows one.
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    org_id = _org()
    other_org_id = _org()
    owner_id, email = _account(org_id)
    c = _client_as(email)
    r = c.post(
        "/meetings",
        data={"title": "t", "organization_id": str(other_org_id)},
        files={"file": ("a.wav", b"raw", "audio/wav")},
    )
    assert r.status_code == 201
    with get_session() as s:
        m = s.get(Meeting, r.json()["meeting_id"])
        assert m.organization_id == org_id, "the caller's own org, never client input"
        assert m.owner_account_id == owner_id


def test_auditor_cannot_create_a_meeting():
    org_id = _org()
    _, email = _account(org_id, role="auditor")
    c = _client_as(email)
    r = c.post("/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")})
    assert r.status_code == 403
