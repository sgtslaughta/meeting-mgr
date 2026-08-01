import uuid

from meeting_mgr.db import get_session
from meeting_mgr.models import Organization
from meeting_mgr.retention import get_policy, upsert_policy


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def test_get_policy_returns_none_when_unset():
    org_id = _org()
    with get_session() as s:
        assert get_policy(s, org_id) is None


def test_upsert_policy_creates_then_updates_the_same_row():
    org_id = _org()
    with get_session() as s:
        p1 = upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)
        row_id = p1.id

    with get_session() as s:
        p2 = upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=90)
        assert p2.id == row_id, (
            "a second call must update the existing row, not create a second one"
        )
        assert p2.meeting_retention_days == 90

    with get_session() as s:
        assert get_policy(s, org_id).meeting_retention_days == 90


def test_upsert_policy_null_and_zero_are_distinguishable():
    """NULL means keep forever, 0 means purge immediately -- coercing one to
    the other silently turns 'keep forever' into 'delete everything'."""
    org_id = _org()
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=0, meeting_retention_days=None)

    with get_session() as s:
        policy = get_policy(s, org_id)
        assert policy.audio_retention_days == 0
        assert policy.audio_retention_days is not None
        assert policy.meeting_retention_days is None


def test_upsert_policy_can_flip_zero_back_to_null():
    org_id = _org()
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=0, meeting_retention_days=0)

    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=None)

    with get_session() as s:
        policy = get_policy(s, org_id)
        assert policy.audio_retention_days is None
        assert policy.meeting_retention_days is None
