import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _unique_email(prefix: str) -> str:
    # Persistent Postgres, no per-test cleanup, suite must pass twice in a
    # row: a fixed email would collide with the row committed last run.
    return f"{prefix}-{uuid.uuid4().hex}@example.com"


def _account(email: str, password="s3cret-pw", role="member") -> int:
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        a = Account(
            organization_id=org.id, email=email, role=role, password_hash=hash_password(password)
        )
        s.add(a)
        s.flush()
        return a.id


def test_login_with_correct_credentials_sets_a_session():
    # Kill: dropping `request.session["account_id"] = ...` from login() —
    # status/body still pass but no session cookie is set.
    email = _unique_email("a")
    _account(email)
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": "s3cret-pw"})
    assert r.status_code == 200
    assert r.json()["email"] == email
    assert "session" in c.cookies


def test_login_with_wrong_password_is_401():
    # Kill: using `==` or skipping verify_password entirely (always allow).
    email = _unique_email("a")
    _account(email)
    r = TestClient(app).post("/auth/login", json={"email": email, "password": "nope"})
    assert r.status_code == 401


def test_login_for_account_with_no_password_is_401():
    # Kill: `verify_password(pw, None)` returning True, or login() skipping
    # the None check and comparing against an empty string.
    email = _unique_email("sso-only")
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        s.add(Account(organization_id=org.id, email=email))
    r = TestClient(app).post("/auth/login", json={"email": email, "password": "anything"})
    assert r.status_code == 401


def test_login_with_unknown_email_is_401_and_indistinguishable():
    # Kill: a different status code or a body that reveals "no such user"
    # for an unknown email vs. a known one with a wrong password.
    unknown_email = _unique_email("nobody")
    known_email = _unique_email("a")
    _account(known_email)

    unknown_resp = TestClient(app).post(
        "/auth/login", json={"email": unknown_email, "password": "anything"}
    )
    wrong_pw_resp = TestClient(app).post(
        "/auth/login", json={"email": known_email, "password": "nope"}
    )
    assert unknown_resp.status_code == wrong_pw_resp.status_code == 401
    assert unknown_resp.json() == wrong_pw_resp.json()


def test_logout_clears_the_session():
    # Kill: logout() not calling request.session.clear() — the account_id
    # would remain in the (still signed, still sent) session cookie.
    email = _unique_email("b")
    _account(email)
    c = TestClient(app)
    c.post("/auth/login", json={"email": email, "password": "s3cret-pw"})
    r = c.post("/auth/logout")
    assert r.status_code == 204
    # GET /auth/me does not exist until Task 7; assert on the session cookie
    # payload directly instead. Strengthen this to a GET /auth/me -> 401
    # check once Task 7 lands.
    assert "account_id" not in c.cookies.get("session", "")
