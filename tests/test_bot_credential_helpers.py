import uuid

from meeting_mgr.auth.password import verify_password
from meeting_mgr.bot_credentials import (
    create_bot_credential,
    get_bot_credential_by_id,
    list_bot_credentials,
    revoke_bot_credential,
)
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id: int) -> int:
    with get_session() as s:
        a = Account(organization_id=org_id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return a.id


def test_create_returns_a_token_whose_secret_verifies_against_the_stored_hash():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        cred, token = create_bot_credential(
            s, org_id, label="zoom-bot-1", owner_account_id=account_id
        )
        s.flush()
        cred_id, token_hash = cred.id, cred.token_hash

    assert token.startswith(f"{cred_id}.")
    secret = token.split(".", 1)[1]
    assert verify_password(secret, token_hash)


def test_two_credentials_get_different_tokens():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        _, token1 = create_bot_credential(s, org_id, label="a", owner_account_id=account_id)
        _, token2 = create_bot_credential(s, org_id, label="b", owner_account_id=account_id)
    assert token1 != token2


def test_get_by_id_returns_none_when_absent():
    with get_session() as s:
        assert get_bot_credential_by_id(s, 999999) is None


def test_list_scopes_by_organization():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_session() as s:
        create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
        create_bot_credential(s, org_b, label="b", owner_account_id=account_b)

    with get_session() as s:
        labels = {c.label for c in list_bot_credentials(s, org_a)}
    assert labels == {"a"}


def test_revoke_sets_revoked_at_and_is_scoped_by_organization():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        cred, _ = create_bot_credential(s, org_id, label="a", owner_account_id=account_id)
        cred_id = cred.id

    with get_session() as s:
        revoked = revoke_bot_credential(s, org_id, cred_id)
        assert revoked is not None
        assert revoked.revoked_at is not None

    with get_session() as s:
        assert revoke_bot_credential(s, org_id + 1, cred_id) is None, (
            "revoke must not cross an organization boundary"
        )
