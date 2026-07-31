import base64
import json
import uuid

import itsdangerous
from fastapi.testclient import TestClient

from meeting_mgr.api import auth as auth_module
from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.config import get_settings
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _decode_session_cookie(value: str) -> dict:
    # Mirrors starlette.middleware.sessions.SessionMiddleware's own decode,
    # so the test proves what account data is actually inside the signed
    # cookie rather than pattern-matching the opaque signed string.
    signer = itsdangerous.TimestampSigner(get_settings().session_secret)
    unsigned = signer.unsign(value.encode(), max_age=14 * 24 * 60 * 60)
    return json.loads(base64.b64decode(unsigned))


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
    # Kill: logout() not calling request.session.clear() (e.g. a bare
    # `return Response(status_code=204)`) — the post-login cookie would
    # never change and would still decode to an account_id. Proven by
    # actually patching logout() to a no-op: this test goes RED (see
    # task-3-report.md for the transcript).
    email = _unique_email("b")
    _account(email)
    c = TestClient(app)
    c.post("/auth/login", json={"email": email, "password": "s3cret-pw"})
    post_login_cookie = c.cookies.get("session")
    assert post_login_cookie is not None
    assert _decode_session_cookie(post_login_cookie).get("account_id") is not None

    r = c.post("/auth/logout")
    assert r.status_code == 204

    post_logout_cookie = c.cookies.get("session")
    assert post_logout_cookie != post_login_cookie
    # Starlette's SessionMiddleware clears a session by re-issuing the
    # cookie with value "null" and an expiry in the past; httpx's cookie
    # jar (used by TestClient) honors that expiry and drops the cookie
    # entirely, so the client-visible effect is that "session" disappears.
    # GET /auth/me does not exist until Task 7 — strengthen this to a
    # GET /auth/me -> 401 check once it does.
    assert post_logout_cookie is None


def test_unknown_email_still_pays_the_full_password_verification_cost(monkeypatch):
    # Kill: the `account is None or not verify_password(...)` short-circuit
    # that skipped verify_password entirely for an unknown email, which
    # would make an unknown address respond faster than a wrong password
    # for a real one — a timing side-channel that re-opens the enumeration
    # leak even though the response body is identical.
    calls = []
    original = auth_module.verify_password

    def spy(password, stored):
        calls.append(stored)
        return original(password, stored)

    monkeypatch.setattr(auth_module, "verify_password", spy)

    email = _unique_email("nobody2")
    TestClient(app).post("/auth/login", json={"email": email, "password": "anything"})

    assert len(calls) == 1
    assert calls[0] == auth_module._DUMMY_HASH


def test_passwordless_account_rejects_even_the_known_dummy_plaintext():
    # The dummy hash's plaintext is public (it's in this repo's source).
    # Kill: gating the passwordless-account rejection on `ok` instead of on
    # `has_password` would let anyone who read the source log into any
    # OIDC/mTLS-only account with this exact string.
    email = _unique_email("sso-only2")
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        s.add(Account(organization_id=org.id, email=email))
    r = TestClient(app).post(
        "/auth/login",
        json={"email": email, "password": "dummy-password-never-used-to-authenticate"},
    )
    assert r.status_code == 401
