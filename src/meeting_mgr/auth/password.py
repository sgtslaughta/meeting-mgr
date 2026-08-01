"""Local password auth: stdlib PBKDF2, no third-party crypto dependency.

Only used for Accounts that opt into a password. OIDC- and mTLS-only
Accounts have password_hash = None and always fail verify_password.
"""

import hashlib
import hmac
import os

_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    salt_hex, sep, digest_hex = stored.partition("$")
    if not sep:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(candidate, expected)
