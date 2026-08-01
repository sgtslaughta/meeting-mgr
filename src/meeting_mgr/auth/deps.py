"""Unifies OIDC, local password, and mTLS into one Account per request.

Precedence: an mTLS header that survived Task 4's middleware is proof of a
trusted proxy's own client-certificate verification, so it is checked first
and, if present, is authoritative. Otherwise the session cookie (set by
either /auth/login or /auth/oidc/callback) identifies the Account.

A trusted mTLS subject with no matching Account is rejected outright (401),
never auto-provisioned — otherwise anyone able to obtain a proxy-accepted
certificate could grant themselves an identity. Likewise a session that
references an Account row that no longer exists must fail closed rather than
raise, since `Session.get` simply returns None for a missing row.
"""

from fastapi import HTTPException, Request

from meeting_mgr.auth.mtls import MTLS_SUBJECT_HEADER
from meeting_mgr.db import get_readonly_session
from meeting_mgr.models import Account


def get_current_account(request: Request) -> Account:
    with get_readonly_session() as s:
        subject = request.headers.get(MTLS_SUBJECT_HEADER)
        account = None
        if subject:
            account = s.query(Account).filter_by(mtls_subject=subject).one_or_none()
        if account is None:
            account_id = request.session.get("account_id")
            if account_id is not None:
                account = s.get(Account, account_id)
        if account is None:
            raise HTTPException(401, "authentication required")
        s.expunge(account)
        return account
