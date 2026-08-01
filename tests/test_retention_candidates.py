import uuid
from datetime import datetime, timedelta

from meeting_mgr.db import get_session
from meeting_mgr.models import ActionItem, KeyTopic, Meeting, Organization, Recording
from meeting_mgr.retention import select_purge_candidates, upsert_policy


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _meeting(org_id: int, *, age_days: int, with_recording: bool = True) -> int:
    with get_session() as s:
        created = datetime.utcnow() - timedelta(days=age_days)
        m = Meeting(organization_id=org_id, title=f"m-{age_days}d", created_at=created)
        s.add(m)
        s.flush()
        if with_recording:
            s.add(Recording(meeting_id=m.id, raw_key=f"raw/{m.id}", normalized_key=f"norm/{m.id}"))
        return m.id


NOW = datetime.utcnow()


def test_no_policy_means_no_candidates():
    org_id = _org()
    _meeting(org_id, age_days=1000)
    with get_session() as s:
        assert select_purge_candidates(s, org_id, now=NOW) == []


def test_meeting_younger_than_the_threshold_is_not_a_candidate():
    org_id = _org()
    _meeting(org_id, age_days=5)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        assert select_purge_candidates(s, org_id, now=NOW) == []


def test_meeting_older_than_meeting_retention_is_a_full_candidate():
    org_id = _org()
    meeting_id = _meeting(org_id, age_days=100)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id, now=NOW)
    assert len(candidates) == 1
    assert candidates[0].meeting_id == meeting_id
    assert candidates[0].kind == "full"


def test_meeting_older_than_audio_retention_is_an_audio_candidate():
    org_id = _org()
    meeting_id = _meeting(org_id, age_days=100)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)
        candidates = select_purge_candidates(s, org_id, now=NOW)
    assert len(candidates) == 1
    assert candidates[0].meeting_id == meeting_id
    assert candidates[0].kind == "audio"


def test_a_meeting_past_both_thresholds_is_only_a_full_candidate_not_both():
    org_id = _org()
    meeting_id = _meeting(org_id, age_days=100)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=10, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id, now=NOW)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "full", (
        "full purge already deletes the audio; listing it twice would double-count in a preview"
    )


def test_meeting_with_no_recording_is_never_an_audio_candidate():
    org_id = _org()
    _meeting(org_id, age_days=100, with_recording=False)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=10, meeting_retention_days=None)
        assert select_purge_candidates(s, org_id, now=NOW) == []


def test_provenance_counts_distinguish_confirmed_from_inferred():
    org_id = _org()
    meeting_id = _meeting(org_id, age_days=100)
    with get_session() as s:
        s.add(KeyTopic(meeting_id=meeting_id, title="a", citations=[1], provenance="confirmed"))
        s.add(KeyTopic(meeting_id=meeting_id, title="b", citations=[2], provenance="inferred"))
        s.add(ActionItem(meeting_id=meeting_id, text="c", citations=[], provenance="confirmed"))
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id, now=NOW)
    counts = candidates[0].provenance_counts
    assert counts["key_topic"] == {"confirmed": 1, "inferred": 1}
    assert counts["action_item"] == {"confirmed": 1}


def test_candidates_from_a_different_organization_are_excluded():
    org_a, org_b = _org(), _org()
    _meeting(org_a, age_days=100)
    with get_session() as s:
        upsert_policy(s, org_a, audio_retention_days=None, meeting_retention_days=30)
        upsert_policy(s, org_b, audio_retention_days=None, meeting_retention_days=30)
        assert select_purge_candidates(s, org_b, now=NOW) == []


def test_limit_bounds_the_page_but_limit_none_sees_the_whole_backlog():
    """The dry-run preview (Task 5) calls with limit=None so it can never
    disagree with what purge_organization will eventually delete -- it just
    may take several bounded batches to get there."""
    org_id = _org()
    for age in (100, 101, 102):
        _meeting(org_id, age_days=age)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        page = select_purge_candidates(s, org_id, now=NOW, limit=2)
        everything = select_purge_candidates(s, org_id, now=NOW, limit=None)
    assert len(page) == 2
    assert len(everything) == 3
    assert {c.meeting_id for c in page} <= {c.meeting_id for c in everything}


def test_a_full_eligible_meeting_pushed_off_the_page_is_not_relisted_as_audio():
    """A Meeting old enough for a full purge, but past this call's `limit`
    on the full page, must still be excluded from the audio branch of the
    same call -- otherwise a bounded batch could list it as an audio
    candidate even though it is really due for a full purge."""
    org_id = _org()
    oldest = _meeting(org_id, age_days=200)  # full-eligible, oldest -> on the page
    pushed_off = _meeting(org_id, age_days=150)  # full-eligible, pushed off the page
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=10, meeting_retention_days=30)
        page = select_purge_candidates(s, org_id, now=NOW, limit=1)
    ids = [c.meeting_id for c in page]
    assert ids == [oldest]
    assert pushed_off not in ids


def test_null_retention_purges_nothing():
    """audio_retention_days=None and meeting_retention_days=None must keep
    the meeting forever, distinct from 0 which purges immediately."""
    org_id = _org()
    _meeting(org_id, age_days=1000)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=None)
        assert select_purge_candidates(s, org_id, now=NOW) == []


def test_zero_retention_purges_everything_eligible():
    """meeting_retention_days=0 means purge immediately -- cutoff == now, so
    any existing meeting (even one only 1 day old) must still be selected."""
    org_id = _org()
    meeting_id = _meeting(org_id, age_days=1)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=0)
        candidates = select_purge_candidates(s, org_id, now=NOW)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "full"
