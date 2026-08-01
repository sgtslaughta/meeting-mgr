import uuid
from datetime import datetime, timedelta

from meeting_mgr.db import get_session
from meeting_mgr.models import (
    ActionItem,
    Attribution,
    AuditLogEntry,
    Meeting,
    Organization,
    Participant,
    Recording,
    Segment,
    SpeakerCluster,
)
from meeting_mgr.retention import upsert_policy
from meeting_mgr.storage import ensure_bucket, get_object, put_object


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


def _full_meeting(org_id: int, *, age_days: int) -> tuple[int, str, str]:
    """A Meeting with a Recording, Segment, SpeakerCluster+Attribution, and
    an ActionItem -- one row in every child table an over-widened bulk
    delete could destroy."""
    ensure_bucket()
    with get_session() as s:
        p = Participant(organization_id=org_id, name=f"p-{uuid.uuid4()}")
        s.add(p)
        s.flush()

        m = Meeting(
            organization_id=org_id,
            title="full",
            created_at=datetime.utcnow() - timedelta(days=age_days),
        )
        s.add(m)
        s.flush()
        raw_key, norm_key = f"raw/{m.id}", f"norm/{m.id}"
        put_object(raw_key, b"raw")
        put_object(norm_key, b"norm")
        s.add(Recording(meeting_id=m.id, raw_key=raw_key, normalized_key=norm_key))
        s.add(Segment(meeting_id=m.id, start_seconds=0, end_seconds=1, text="hi"))
        cluster = SpeakerCluster(meeting_id=m.id, label="SPEAKER_00", spans=[])
        s.add(cluster)
        s.flush()
        s.add(Attribution(cluster_id=cluster.id, participant_id=p.id, provenance="confirmed"))
        s.add(ActionItem(meeting_id=m.id, text="do it", citations=[1], provenance="confirmed"))
        return m.id, raw_key, norm_key


def test_purge_organization_does_not_touch_a_sibling_meeting_in_the_same_org():
    """RLS scopes by organization, not by meeting -- a bulk-path filter
    mistake that widens a delete from the candidate's meeting_id to the
    whole organization would pass every existing test here: the
    every-eligible test has no survivor to check, the below-threshold test's
    candidate list is empty so the loop body never runs, and the cross-org
    test cannot catch an intra-org widening by construction (RLS only stops
    at the tenant boundary). Guard the loop-body case directly, with a real
    non-candidate sibling holding data in every child table."""
    from meeting_mgr.pipeline.purge import purge_organization

    org_id = _org()
    old_id, _, _ = _full_meeting(org_id, age_days=100)
    young_id, young_raw_key, young_norm_key = _full_meeting(org_id, age_days=1)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    purge_organization(org_id)

    with get_session() as s:
        assert s.get(Meeting, old_id) is None

        assert s.query(Meeting).filter_by(id=young_id).count() == 1
        assert s.query(Recording).filter_by(meeting_id=young_id).count() == 1
        assert s.query(Segment).filter_by(meeting_id=young_id).count() == 1
        assert s.query(SpeakerCluster).filter_by(meeting_id=young_id).count() == 1
        assert s.query(ActionItem).filter_by(meeting_id=young_id).count() == 1
        young_cluster_ids = [
            c.id for c in s.query(SpeakerCluster).filter_by(meeting_id=young_id).all()
        ]
        assert (
            s.query(Attribution).filter(Attribution.cluster_id.in_(young_cluster_ids)).count() == 1
        )

    assert get_object(young_raw_key) == b"raw"
    assert get_object(young_norm_key) == b"norm"


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
