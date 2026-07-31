from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from meeting_mgr.auth.password import verify_password
from meeting_mgr.db import get_readonly_session
from meeting_mgr.models import Account

router = APIRouter(prefix="/auth")


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
        if account is None or not verify_password(body.password, account.password_hash):
            raise HTTPException(401, "invalid credentials")
        view = _view(account)
    request.session["account_id"] = view["id"]
    return view


@router.post("/logout", status_code=204)
def logout(request: Request):
    request.session.clear()
    return Response(status_code=204)
