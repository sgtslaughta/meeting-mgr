import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization


def _org(s) -> int:
    return s.query(Organization).filter_by(name="default").one().id


def _unique_email() -> str:
    # The test suite runs against a persistent Postgres with no per-test
    # cleanup, and must pass twice in a row. A hard-coded email would collide
    # with the row this same test committed on the previous run, so each
    # invocation gets a fresh address instead.
    return f"acct-{uuid.uuid4().hex}@example.com"


def test_account_defaults_to_member_role():
    with get_session() as s:
        a = Account(organization_id=_org(s), email=_unique_email())
        s.add(a)
        s.flush()
        assert a.role == "member"


def test_email_is_unique_within_an_organization():
    dup_email = _unique_email()
    with pytest.raises(IntegrityError):
        with get_session() as s:
            org_id = _org(s)
            s.add(Account(organization_id=org_id, email=dup_email))
            s.add(Account(organization_id=org_id, email=dup_email))
            s.flush()


def test_oidc_subject_is_globally_unique():
    oidc_subject = f"sub-{uuid.uuid4().hex}"
    with pytest.raises(IntegrityError):
        with get_session() as s:
            org_id = _org(s)
            s.add(Account(organization_id=org_id, email=_unique_email(), oidc_subject=oidc_subject))
            s.add(Account(organization_id=org_id, email=_unique_email(), oidc_subject=oidc_subject))
            s.flush()
