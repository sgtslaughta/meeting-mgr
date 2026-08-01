import uuid
from datetime import datetime, timedelta

from meeting_mgr.db import get_session
from meeting_mgr.models import AuditLogEntry, Meeting, Organization, Recording
from meeting_mgr.retention import upsert_policy
from meeting_mgr.storage import ensure_bucket, put_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _old_meeting(org_id: int, *, age_days: int, with_recording: bool = True, title="t") -> int:
    ensure_bucket()
    with get_session() as s:
        m = Meeting(
            organization_id=org_id,
            title=title,
            created_at=datetime.utcnow() - timedelta(days=age_days),
        )
        s.add(m)
        s.flush()
        if with_recording:
            key = f"raw/{m.id}"
            put_object(key, b"data")
            s.add(Recording(meeting_id=m.id, raw_key=key))
        return m.id


def test_purge_organization_purges_every_eligible_meeting():
    from meeting_mgr.pipeline.purge import purge_organization

    org_id = _org()
    m1 = _old_meeting(org_id, age_days=100)
    m2 = _old_meeting(org_id, age_days=200)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    purge_organization(org_id)

    with get_session() as s:
        assert s.get(Meeting, m1) is None
        assert s.get(Meeting, m2) is None


def test_purge_organization_does_not_touch_meetings_below_threshold():
    from meeting_mgr.pipeline.purge import purge_organization

    org_id = _org()
    young = _old_meeting(org_id, age_days=1)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    purge_organization(org_id)

    with get_session() as s:
        assert s.get(Meeting, young) is not None


def test_purge_organization_does_not_touch_another_organizations_meetings():
    from meeting_mgr.pipeline.purge import purge_organization

    org_a, org_b = _org(), _org()
    other_org_meeting = _old_meeting(org_b, age_days=100)
    with get_session() as s:
        upsert_policy(s, org_a, audio_retention_days=None, meeting_retention_days=30)

    purge_organization(org_a)

    with get_session() as s:
        assert s.get(Meeting, other_org_meeting) is not None


def test_purge_organization_continues_past_one_failing_meeting(monkeypatch, caplog):
    """A failure purging one candidate must not stop the rest -- same
    posture as run_pipeline()'s OPTIONAL_STAGES handling -- and the failure
    must be logged, not silently discarded, so an operator can see a Meeting
    that keeps failing every sweep."""
    import logging

    import meeting_mgr.pipeline.purge as purge_mod

    org_id = _org()
    bad = _old_meeting(org_id, age_days=100, title="bad")
    good = _old_meeting(org_id, age_days=100, title="good")
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    real_purge = purge_mod.purge_meeting_full

    def flaky(org_id_arg, meeting_id):
        if meeting_id == bad:
            raise RuntimeError("simulated storage outage")
        real_purge(org_id_arg, meeting_id)

    monkeypatch.setattr(purge_mod, "purge_meeting_full", flaky)

    with caplog.at_level(logging.ERROR, logger="meeting_mgr.pipeline.purge"):
        purge_mod.purge_organization(org_id)

    with get_session() as s:
        assert s.get(Meeting, bad) is not None, "the failing meeting must remain for the next sweep"
        assert s.get(Meeting, good) is None, "one failure must not block the rest of the batch"
    assert str(bad) in caplog.text and str(org_id) in caplog.text, (
        "the failure must be logged with meeting_id and organization_id, not swallowed silently"
    )


def test_purge_organization_respects_batch_limit(monkeypatch):
    """Kill: a `purge_organization` that ignores the bounded query and
    purges everything eligible in one pass would leave 0 remaining, not 3."""
    import meeting_mgr.pipeline.purge as purge_mod
    from meeting_mgr.retention import select_purge_candidates as real_select

    org_id = _org()
    ids = [
        _old_meeting(org_id, age_days=100 + i, with_recording=False, title=f"batch-{i}")
        for i in range(5)
    ]
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    # select_purge_candidates() (Task 4) already bounds by `limit`; pin it to
    # 2 here rather than creating 501 meetings to exercise the real default.
    monkeypatch.setattr(
        purge_mod,
        "select_purge_candidates",
        lambda s, org_id, **kw: real_select(s, org_id, limit=2),
    )

    purge_mod.purge_organization(org_id)

    with get_session() as s:
        remaining = s.query(Meeting).filter(Meeting.id.in_(ids)).count()
    assert remaining == 3, "exactly 3 of 5 eligible meetings must survive a limit=2 batch"


def test_purge_organization_purges_meeting_eligible_for_both_kinds_once_as_full():
    """Kill: a purge_organization that purges an over-both-thresholds
    meeting via both the full and audio branches would raise on the second
    (already-deleted) attempt, or record two audit entries instead of one."""
    from meeting_mgr.pipeline.purge import purge_organization

    org_id = _org()
    meeting_id = _old_meeting(org_id, age_days=200, with_recording=True)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=30)

    purge_organization(org_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        entries = (
            s.query(AuditLogEntry).filter(AuditLogEntry.target == f"meeting:{meeting_id}").all()
        )
    assert len(entries) == 1, "a both-thresholds meeting must be audited exactly once"
    assert entries[0].action == "meeting.purge.full"


def test_purge_organization_with_no_policy_does_nothing():
    from meeting_mgr.pipeline.purge import purge_organization

    org_id = _org()
    meeting_id = _old_meeting(org_id, age_days=1000)

    purge_organization(org_id)  # no policy configured

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None
