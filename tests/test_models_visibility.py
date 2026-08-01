import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, MeetingShare, Organization


def _org(s) -> int:
    return s.query(Organization).filter_by(name="default").one().id


def _unique_email() -> str:
    # The suite runs against a persistent Postgres with no per-test cleanup
    # and must pass twice in a row. A hard-coded email would collide with
    # the row this same test committed on a previous run, masking which
    # constraint actually fired. See tests/test_models_account.py.
    return f"share-{uuid.uuid4().hex}@example.com"


def test_meeting_defaults_to_private():
    with get_session() as s:
        m = Meeting(organization_id=_org(s), title="t")
        s.add(m)
        s.flush()
        assert m.visibility == "private"
        assert m.owner_account_id is None


def test_meeting_share_is_unique_per_account():
    with pytest.raises(IntegrityError):
        with get_session() as s:
            org_id = _org(s)
            m = Meeting(organization_id=org_id, title="t")
            a = Account(organization_id=org_id, email=_unique_email())
            s.add_all([m, a])
            s.flush()
            s.add(MeetingShare(meeting_id=m.id, account_id=a.id))
            s.add(MeetingShare(meeting_id=m.id, account_id=a.id))
            s.flush()
