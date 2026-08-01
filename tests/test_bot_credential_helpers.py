import uuid

from meeting_mgr.auth.password import verify_password
from meeting_mgr.bot_credentials import (
    create_bot_credential,
    list_bot_credentials,
    revoke_bot_credential,
)
from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Account, BotCredential, Organization


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


def test_list_excludes_other_orgs_rows_under_the_org_scoped_session():
    """Runs through get_org_session -- the least-privilege path admin
    endpoints (Task 3) will actually use -- rather than get_session's
    RLS-exempt superuser connection. NOT an RLS test: the org_id argument
    here agrees with the session's own org, so the app-layer
    filter_by(organization_id=...) alone excludes the other org's row --
    confirmed by mutating the policy to USING (true) and seeing this test
    stay green. This pins the app-layer filter under a realistic session
    type, nothing more."""
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
    """Genuine RLS test: the org_id argument (org_b) names org_b's row's
    *real* organization, so the app-layer filter_by(organization_id=org_b)
    would match it on its own -- only RLS (session scoped to org_a) blocks
    it. This is the one configuration where the app-layer filter cannot
    subsume RLS, so it is the load-bearing case. Verified by mutating the
    policy to USING (true)/WITH CHECK (true): this test goes red while
    test_list_excludes_other_orgs_rows_under_the_org_scoped_session (above)
    stays green. If the org_id argument is ever "simplified" to agree with
    the session's own org, this stops testing RLS -- don't do that."""
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

    # Genuine RLS test: the org_id argument (org_b) names cred_b's *real*
    # organization, so the app-layer filter_by(id=cred_b_id,
    # organization_id=org_b) would match it on its own -- only RLS (session
    # scoped to org_a) blocks it. Verified by mutating the policy to
    # USING (true)/WITH CHECK (true): this assertion goes red while
    # test_revoke_requires_credential_id_and_org_id_to_agree (below) stays
    # green. Don't "simplify" org_b here to org_a -- that would destroy the
    # only RLS coverage this test has.
    with get_org_session(org_a) as s:
        assert revoke_bot_credential(s, org_b, cred_b_id) is None, (
            "RLS did not confine bot_credential to its own organization"
        )

    with get_org_session(org_a) as s:
        revoked = revoke_bot_credential(s, org_a, cred_a_id)
        assert revoked is not None
        assert revoked.revoked_at is not None


def test_revoke_requires_credential_id_and_org_id_to_agree():
    """NOT an RLS test: cred_a_id belongs to org_a, and the session is also
    org_a, so RLS would happily let this row through. The org_id argument
    passed (org_b) disagrees with the credential's *real* organization, so
    the app-layer predicate id=cred_a_id AND organization_id=org_b never
    matches regardless of RLS -- confirmed by mutating the policy to
    USING (true) and seeing this test stay green. This pins the app-layer
    filter, nothing more."""
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_org_session(org_a) as s:
        cred_a, _ = create_bot_credential(s, org_a, label="a", owner_account_id=account_a)
        cred_a_id = cred_a.id
    with get_org_session(org_b) as s:
        create_bot_credential(s, org_b, label="b", owner_account_id=account_b)

    with get_org_session(org_a) as s:
        assert revoke_bot_credential(s, org_b, cred_a_id) is None

    with get_session() as s:
        untouched = s.get(BotCredential, cred_a_id)
    assert untouched.revoked_at is None, "disagreeing org_id must not revoke anything"
