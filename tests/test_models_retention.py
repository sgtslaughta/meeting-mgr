import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Organization, RetentionPolicy


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def test_retention_policy_defaults_to_keep_forever():
    org_id = _org()
    with get_session() as s:
        p = RetentionPolicy(organization_id=org_id)
        s.add(p)
        s.flush()
        assert p.audio_retention_days is None
        assert p.meeting_retention_days is None


def test_retention_policy_is_unique_per_organization():
    org_id = _org()
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(RetentionPolicy(organization_id=org_id, audio_retention_days=30))
            s.add(RetentionPolicy(organization_id=org_id, audio_retention_days=60))
            s.flush()


def test_retention_policy_rejects_negative_audio_retention_days():
    """A negative audio_retention_days would put the purge cutoff
    (now - retention_days) in the future, making every Meeting eligible for
    deletion -- must be rejected at the DB level."""
    org_id = _org()
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(RetentionPolicy(organization_id=org_id, audio_retention_days=-1))
            s.flush()


def test_retention_policy_rejects_negative_meeting_retention_days():
    org_id = _org()
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(RetentionPolicy(organization_id=org_id, meeting_retention_days=-1))
            s.flush()


def test_retention_policy_tenant_isolation():
    """A raw SELECT with no application-layer filter must still be confined
    to its own organization -- proves the RLS policy and the meeting_app
    grant both exist, not just the table."""
    org_a, org_b = _org(), _org()
    with get_session() as s:
        s.add(RetentionPolicy(organization_id=org_a, audio_retention_days=10))
        s.add(RetentionPolicy(organization_id=org_b, audio_retention_days=20))

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT audio_retention_days FROM retention_policy")).fetchall()
        values = {r[0] for r in rows}
    assert 10 in values
    assert 20 not in values, "RLS did not confine retention_policy to its own organization"
