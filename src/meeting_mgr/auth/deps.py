"""Unifies OIDC, local password, and mTLS into one Account per request.

Precedence: an mTLS header that survived Task 4's middleware is proof of a
trusted proxy's own client-certificate verification, so it is checked first.
When the subject matches an Account, that match is authoritative. When it
matches no Account, authentication falls back to the session cookie (set by
either /auth/login or /auth/oidc/callback) instead of hard-failing the
request. A request with neither a matching mTLS subject nor a valid session
is rejected (401).

The fallback is deliberate, not a weakening: Task 4 guarantees a client
cannot forge this header past a non-allowlisted peer, and the session path
is independently authenticated (password or OIDC). Hard-failing on an
unmatched-but-trusted subject would mean anyone whose certificate is
reissued — so their `mtls_subject` no longer matches the Account row — is
locked out of an otherwise-valid password session until an admin
intervenes, which is a worse failure mode than the one a hard fail would
prevent. What the code never does, under any header value, is
auto-provision an Account for an unmatched subject — a trusted header alone
can select an existing identity, never create one.

A session that references an Account row that no longer exists must fail
closed rather than raise, since `Session.get` simply returns None for a
missing row.
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
