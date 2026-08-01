from fastapi.testclient import TestClient

from meeting_mgr.api.auth import oauth
from meeting_mgr.api.main import app
from meeting_mgr.db import get_session
from meeting_mgr.models import Account


def test_login_redirects_to_the_provider(monkeypatch):
    async def fake_redirect(request, redirect_uri):
        from starlette.responses import RedirectResponse

        return RedirectResponse("https://idp.example.com/authorize?state=abc")

    monkeypatch.setattr(oauth.oidc, "authorize_redirect", fake_redirect)
    r = TestClient(app).get("/auth/oidc/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"].startswith("https://idp.example.com/")


def test_callback_creates_an_account_on_first_login(monkeypatch):
    async def fake_token(request):
        return {"userinfo": {"sub": "provider-sub-1", "email": "new@example.com"}}

    monkeypatch.setattr(oauth.oidc, "authorize_access_token", fake_token)
    c = TestClient(app)
    r = c.get("/auth/oidc/callback", follow_redirects=False)
    assert r.status_code in (302, 307)
    with get_session() as s:
        acct = s.query(Account).filter_by(oidc_subject="provider-sub-1").one()
        assert acct.email == "new@example.com"
        assert acct.role == "member"
    me = c.get("/auth/me")
    assert me.json()["email"] == "new@example.com"


def test_callback_reuses_the_existing_account_on_second_login(monkeypatch):
    async def fake_token(request):
        return {"userinfo": {"sub": "provider-sub-2", "email": "repeat@example.com"}}

    monkeypatch.setattr(oauth.oidc, "authorize_access_token", fake_token)
    c = TestClient(app)
    c.get("/auth/oidc/callback", follow_redirects=False)
    c.get("/auth/oidc/callback", follow_redirects=False)
    with get_session() as s:
        rows = s.query(Account).filter_by(oidc_subject="provider-sub-2").all()
        assert len(rows) == 1, "a second login must not create a second Account"


def test_callback_without_email_claim_is_400(monkeypatch):
    async def fake_token(request):
        return {"userinfo": {"sub": "provider-sub-3"}}

    monkeypatch.setattr(oauth.oidc, "authorize_access_token", fake_token)
    r = TestClient(app).get("/auth/oidc/callback")
    assert r.status_code == 400
