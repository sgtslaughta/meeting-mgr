import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.auth.mtls import MTLS_SUBJECT_HEADER, MTLSHeaderStripMiddleware
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _unique(prefix: str) -> str:
    # Persistent Postgres, no per-test cleanup, suite must pass twice in a
    # row: a fixed value would collide with the row committed last run.
    # mtls_subject/oidc_subject/email are all globally-or-org unique.
    return f"{prefix}-{uuid.uuid4().hex}"


def _account(**kw) -> int:
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        a = Account(organization_id=org.id, **kw)
        s.add(a)
        s.flush()
        return a.id


def _app(allowlist=frozenset({"testclient"})):
    app = FastAPI()
    app.add_middleware(MTLSHeaderStripMiddleware, allowlist=allowlist)
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.get("/whoami")
    def whoami(account: Account = Depends(get_current_account)):
        return {"id": account.id, "email": account.email}

    return app


def _app_with_login(allowlist=frozenset({"testclient"})):
    app = FastAPI()
    app.add_middleware(MTLSHeaderStripMiddleware, allowlist=allowlist)
    app.add_middleware(SessionMiddleware, secret_key="test")

    @app.post("/login/{account_id}")
    def login(account_id: int, request: Request):
        request.session["account_id"] = account_id
        return {}

    @app.get("/whoami")
    def whoami(account: Account = Depends(get_current_account)):
        return {"id": account.id, "email": account.email}

    return app


def test_no_credentials_is_401():
    # Kill: get_current_account returning None or skipping the raise.
    r = TestClient(_app()).get("/whoami")
    assert r.status_code == 401


def test_trusted_mtls_header_resolves_the_account():
    # Kill: dropping the mTLS branch in get_current_account.
    subject = _unique("cn=mtls")
    email = f"mtls-{uuid.uuid4().hex}@example.com"
    _account(email=email, mtls_subject=subject)
    r = TestClient(_app()).get("/whoami", headers={MTLS_SUBJECT_HEADER: subject})
    assert r.status_code == 200
    assert r.json()["email"] == email


def test_stripped_mtls_header_from_untrusted_source_is_401():
    # Kill: get_current_account trusting the header regardless of whether
    # the strip middleware removed it (i.e. duplicating the allowlist check
    # here would mask this, but so would ignoring the strip's effect).
    subject = _unique("cn=mtls2")
    email = f"mtls2-{uuid.uuid4().hex}@example.com"
    _account(email=email, mtls_subject=subject)
    r = TestClient(_app(allowlist=frozenset())).get(
        "/whoami", headers={MTLS_SUBJECT_HEADER: subject}
    )
    assert r.status_code == 401, (
        "the header was stripped upstream — this must behave exactly like no header at all"
    )


def test_mtls_subject_with_no_matching_account_is_401():
    # Kill: auto-provisioning an Account for an unrecognized-but-trusted
    # mTLS subject instead of rejecting it.
    r = TestClient(_app()).get("/whoami", headers={MTLS_SUBJECT_HEADER: _unique("cn=nobody")})
    assert r.status_code == 401


def test_session_only_resolves_the_right_account():
    # Kill: get_current_account never consulting request.session at all.
    email = f"sess-{uuid.uuid4().hex}@example.com"
    account_id = _account(email=email)
    c = TestClient(_app_with_login())
    c.post(f"/login/{account_id}")
    r = c.get("/whoami")
    assert r.status_code == 200
    assert r.json()["id"] == account_id
    assert r.json()["email"] == email


def test_both_present_mtls_header_wins_over_session():
    # Documented precedence: a trusted mTLS header is checked first and is
    # authoritative when it resolves to an Account. Kill: swapping the
    # order so the session is consulted first.
    session_email = f"sessowner-{uuid.uuid4().hex}@example.com"
    session_account_id = _account(email=session_email)

    mtls_subject = _unique("cn=mtlswinner")
    mtls_email = f"mtlswinner-{uuid.uuid4().hex}@example.com"
    mtls_account_id = _account(email=mtls_email, mtls_subject=mtls_subject)

    c = TestClient(_app_with_login())
    c.post(f"/login/{session_account_id}")
    r = c.get("/whoami", headers={MTLS_SUBJECT_HEADER: mtls_subject})
    assert r.status_code == 200
    assert r.json()["id"] == mtls_account_id
    assert r.json()["id"] != session_account_id


def test_session_referencing_deleted_account_is_401_not_500():
    # Kill: get_current_account raising (e.g. AttributeError on a None
    # account) instead of failing closed with 401.
    email = f"ghost-{uuid.uuid4().hex}@example.com"
    account_id = _account(email=email)
    with get_session() as s:
        s.query(Account).filter_by(id=account_id).delete()

    c = TestClient(_app_with_login())
    c.post(f"/login/{account_id}")
    r = c.get("/whoami")
    assert r.status_code == 401
