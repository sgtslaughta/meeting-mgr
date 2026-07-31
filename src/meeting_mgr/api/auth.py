from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from meeting_mgr.auth.password import hash_password, verify_password
from meeting_mgr.db import get_readonly_session
from meeting_mgr.models import Account

router = APIRouter(prefix="/auth")

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
