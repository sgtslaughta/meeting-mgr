import uuid

from meeting_mgr.auth.password import verify_password
from meeting_mgr.bot_credentials import (
    create_bot_credential,
    get_bot_credential_by_id,
    list_bot_credentials,
    revoke_bot_credential,
)
from meeting_mgr.db import get_org_session, get_session
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


def test_get_by_id_returns_the_matching_row():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        cred, _ = create_bot_credential(s, org_id, label="zoom-bot-1", owner_account_id=account_id)
        cred_id = cred.id

    with get_session() as s:
        found = get_bot_credential_by_id(s, cred_id)
    assert found is not None
    assert found.id == cred_id
    assert found.label == "zoom-bot-1"
    assert found.organization_id == org_id


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


def test_list_is_confined_by_rls_under_the_org_scoped_session():
    """Runs through get_org_session -- the RLS-enforced, least-privilege
    path admin endpoints (Task 3) will actually use -- not get_session's
    RLS-exempt superuser connection. Proves list_bot_credentials cannot see
    another org's rows even before the app-layer filter is considered."""
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_org_session(org_a) as s:
        create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
    with get_org_session(org_b) as s:
        create_bot_credential(s, org_b, label="b", owner_account_id=account_b)

    with get_org_session(org_a) as s:
        labels = {c.label for c in list_bot_credentials(s, org_a)}
    assert labels == {"a"}, "RLS did not confine bot_credential to its own organization"


def test_list_with_org_id_argument_disagreeing_with_session_org_returns_nothing():
    """The RLS session is scoped to org_a via app.org_id, but the org_id
    argument passed in disagrees (asks for org_b). The two predicates
    intersect, so this must fail closed -- neither org's rows leak out."""
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_org_session(org_a) as s:
        create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
    with get_org_session(org_b) as s:
        create_bot_credential(s, org_b, label="b", owner_account_id=account_b)

    with get_org_session(org_a) as s:
        labels = {c.label for c in list_bot_credentials(s, org_b)}
    assert labels == set()


def test_revoke_is_confined_by_rls_under_the_org_scoped_session():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_org_session(org_a) as s:
        cred_a, _ = create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
        cred_a_id = cred_a.id
    with get_org_session(org_b) as s:
        cred_b, _ = create_bot_credential(s, org_b, label="b", owner_account_id=account_b)
        cred_b_id = cred_b.id

    # org_a's session cannot revoke org_b's credential, even though the
    # app-layer org_id argument passed is org_b's own id -- RLS hides the
    # row from this session before the app-layer filter even runs.
    with get_org_session(org_a) as s:
        assert revoke_bot_credential(s, org_b, cred_b_id) is None, (
            "RLS did not confine bot_credential to its own organization"
        )

    with get_org_session(org_a) as s:
        revoked = revoke_bot_credential(s, org_a, cred_a_id)
        assert revoked is not None
        assert revoked.revoked_at is not None


def test_revoke_with_org_id_argument_disagreeing_with_session_org_revokes_nothing():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_org_session(org_a) as s:
        cred_a, _ = create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
        cred_a_id = cred_a.id
    with get_org_session(org_b) as s:
        create_bot_credential(s, org_b, label="b", owner_account_id=account_b)

    # Session is scoped to org_a, but the org_id argument disagrees (org_b).
    # Even though cred_a_id is visible to this session under RLS, the
    # app-layer predicate org_id == org_b never matches it.
    with get_org_session(org_a) as s:
        assert revoke_bot_credential(s, org_b, cred_a_id) is None

    with get_session() as s:
        untouched = get_bot_credential_by_id(s, cred_a_id)
    assert untouched.revoked_at is None, "disagreeing org_id must not revoke anything"
