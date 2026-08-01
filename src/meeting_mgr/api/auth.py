from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.auth.oidc import build_oauth
from meeting_mgr.auth.password import hash_password, verify_password
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Account, Organization

router = APIRouter(prefix="/auth")

oauth = build_oauth()

# A fixed, valid-format hash with no known plaintext. Used in place of a
# missing account/password_hash so `verify_password` always does the same
# PBKDF2 work regardless of whether the email exists — an unknown email
# must not respond faster than a wrong password, or the timing itself
# becomes an account-enumeration oracle even though the response body
# doesn't.
_DUMMY_HASH = hash_password("dummy-password-never-used-to-authenticate")


class LoginIn(BaseModel):
    email: str
    password: str


def _view(account: Account) -> dict:
    return {
        "id": account.id,
        "email": account.email,
        "role": account.role,
        "organization_id": account.organization_id,
    }


@router.post("/login")
def login(body: LoginIn, request: Request):
    # Untenanted, RLS-bypassing session: no org is known yet, only an email,
    # so this can't be scoped by tenant. New post-auth code must use the
    # org-scoped sessions instead (see issue #37).
    with get_readonly_session() as s:
        account = s.query(Account).filter_by(email=body.email).one_or_none()
        has_password = account is not None and account.password_hash is not None
        # Always run PBKDF2 against *some* valid-format hash, even for an
        # unknown email or a passwordless (OIDC/mTLS-only) account, so
        # response time can't distinguish those cases from "wrong
        # password". `has_password` (not `ok`) alone gates the passwordless
        # case, so a lucky guess against the dummy hash can never log in.
        stored_hash = account.password_hash if has_password else _DUMMY_HASH
        ok = verify_password(body.password, stored_hash)
        if not has_password or not ok:
            raise HTTPException(401, "invalid credentials")
        view = _view(account)
    request.session["account_id"] = view["id"]
    return view


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)


@router.get("/me")
def me(account: Account = Depends(get_current_account)):
    return _view(account)


@router.get("/oidc/login")
async def oidc_login(request: Request):
    redirect_uri = str(request.url_for("oidc_callback"))
    return await oauth.oidc.authorize_redirect(request, redirect_uri)


@router.get("/oidc/callback")
async def oidc_callback(request: Request):
    # authorize_access_token validates the `state` parameter against the
    # value authlib stashed in the session during authorize_redirect — this
    # is the CSRF defence for the authorization-code flow. We rely on
    # authlib's own validation here rather than checking `state` ourselves.
    token = await oauth.oidc.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    subject, email = claims.get("sub"), claims.get("email")
    if not subject or not email:
        raise HTTPException(400, "OIDC provider did not return sub and email claims")

    # Untenanted, RLS-bypassing session: identity bootstrap, same reasoning
    # as login() above -- the tenant isn't known until the Account is found.
    with get_session() as s:
        account = s.query(Account).filter_by(oidc_subject=subject).one_or_none()
        if account is None:
            org = s.query(Organization).filter_by(name="default").one()
            account = Account(
                organization_id=org.id, email=email, oidc_subject=subject, role="member"
            )
            s.add(account)
            s.flush()
        account_id = account.id

    request.session["account_id"] = account_id
    return RedirectResponse("/")
