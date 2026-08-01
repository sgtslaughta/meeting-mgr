"""Resolves a bot's bearer token to its BotCredential row -- the meeting-bot
analogue of auth/deps.py's get_current_account. Never returns an Account,
never touches request.session: a bot has no session and no browser.

Untenanted, RLS-bypassing session, same as get_current_account -- this runs
before we know which Organization the request belongs to. See db.py's
get_session()/get_readonly_session() docstrings, which list this as the
fourth of the narrow identity-bootstrap exceptions.

Token shape: "{credential_id}.{secret}" (see bot_credentials.py). Because
hash_password salts every call, a credential cannot be looked up by its
hash -- the id must be read first and the row loaded, then the secret
verified against that row's token_hash. That two-step lookup is exactly
what creates a timing oracle: a malformed token, an unknown id, or a
revoked credential can all be rejected *before* any PBKDF2 work runs,
making "no such credential" measurably faster than "wrong secret" -- an
id-enumeration oracle. This module closes it the same way api/auth.py's
login() closes the account-enumeration oracle: every rejection path runs
verify_password against *some* valid-format hash (the row's real hash when
one exists and is usable, a fixed dummy hash otherwise), and only a
`valid` flag -- never the verify_password result alone -- gates success.
That flag is what stops a lucky guess against the dummy hash from ever
authenticating.
"""

from fastapi import HTTPException, Request

from meeting_mgr.auth.password import hash_password, verify_password
from meeting_mgr.db import get_readonly_session
from meeting_mgr.models import BotCredential

_UNAUTHORIZED = HTTPException(401, "invalid bot credential")

# Fixed, valid-format hash with no known plaintext. Stood in for a missing
# or unusable token_hash (malformed token, unknown id, revoked credential)
# so verify_password always does the same PBKDF2 work -- see module
# docstring.
_DUMMY_HASH = hash_password("dummy-bot-secret-never-used-to-authenticate")


def get_bot_credential(request: Request) -> BotCredential:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
    credential_id_s, sep, secret = token.partition(".")

    # Untenanted, RLS-bypassing session: no org is known yet, only a bearer
    # token, so this can't be scoped by tenant (see db.py's get_session()
    # docstring).
    with get_readonly_session() as s:
        cred = None
        if sep and credential_id_s.isdigit():
            cred = s.get(BotCredential, int(credential_id_s))
        valid = cred is not None and cred.revoked_at is None
        stored_hash = cred.token_hash if valid else _DUMMY_HASH
        # Always call verify_password, on every path, against a valid-format
        # hash -- `valid` (not the verify_password result) alone gates
        # success, so a guess that happens to match the dummy hash can never
        # authenticate.
        ok = verify_password(secret, stored_hash)
        if not valid or not ok:
            raise _UNAUTHORIZED
        s.expunge(cred)
        return cred
