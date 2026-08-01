"""Bot credential lifecycle -- pure helpers, no side effects beyond the
Session passed in. Used by both the admin API (api/bot_credentials.py) and
the auth dependency (auth/bot_deps.py) that resolves a token back to a row.

Token shape: "{credential_id}.{secret}". The id prefix makes lookup O(1) --
a single get(BotCredential, id) -- instead of scanning every row and
re-hashing the presented secret against each one's salt. The secret itself
is never stored; only hash_password(secret) is, in token_hash.
"""

import secrets
from datetime import datetime

from meeting_mgr.auth.password import hash_password
from meeting_mgr.models import BotCredential


def create_bot_credential(
    s, org_id: int, *, label: str, owner_account_id: int
) -> tuple[BotCredential, str]:
    """Mint a new credential. Returns the row and the plaintext token --
    the only time the secret is ever available; only its hash is stored."""
    secret = secrets.token_urlsafe(32)
    cred = BotCredential(
        organization_id=org_id,
        owner_account_id=owner_account_id,
        label=label,
        token_hash="",
    )
    s.add(cred)
    s.flush()  # assigns cred.id, needed for the token prefix
    cred.token_hash = hash_password(secret)
    s.flush()
    return cred, f"{cred.id}.{secret}"


def list_bot_credentials(s, org_id: int) -> list[BotCredential]:
    return s.query(BotCredential).filter_by(organization_id=org_id).order_by(BotCredential.id).all()


def revoke_bot_credential(s, org_id: int, credential_id: int) -> BotCredential | None:
    """Sets revoked_at. Returns None if no such credential exists in that
    Organization -- including if it exists in a different one.

    Idempotent: revoking an already-revoked credential overwrites
    revoked_at with the current timestamp and returns the row (not None).
    The end state -- revoked -- is unchanged either way, so callers (e.g.
    Task 3's endpoint) should treat a repeat revoke as success, not a
    conflict."""
    cred = s.query(BotCredential).filter_by(id=credential_id, organization_id=org_id).one_or_none()
    if cred is None:
        return None
    cred.revoked_at = datetime.utcnow()
    s.flush()
    return cred
