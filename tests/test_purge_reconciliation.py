import uuid
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Meeting, Organization, Recording, Segment
from meeting_mgr.pipeline.purge import _purge_audio_objects, purge_meeting_audio, purge_meeting_full
from meeting_mgr.retention import select_purge_candidates, upsert_policy
from meeting_mgr.storage import ensure_bucket, get_object, put_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _meeting_with_audio(org_id: int) -> int:
    ensure_bucket()
    with get_session() as s:
        m = Meeting(organization_id=org_id, title="t")
        s.add(m)
        s.flush()
        raw_key, norm_key = f"raw/{m.id}", f"norm/{m.id}"
        put_object(raw_key, b"raw")
        put_object(norm_key, b"norm")
        s.add(Recording(meeting_id=m.id, raw_key=raw_key, normalized_key=norm_key))
        return m.id


def test_a_crash_after_storage_delete_but_before_db_delete_is_recovered_by_a_second_run():
    """Simulates a process crash between purge_meeting_full's two steps: call
    only the storage half directly (as _purge_audio_objects does internally),
    leave the Meeting row alone, then prove that re-running the full,
    public purge_meeting_full() finishes the job -- deleting an
    already-gone S3 key must not raise, and the Meeting row must still end
    up deleted."""
    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    # Step 1 of purge_meeting_full, run in isolation -- the "crash" happens
    # right after this, before the DB delete that would normally follow it
    # in the same function call.
    with get_org_session(org_id) as s:
        keys_first_pass = _purge_audio_objects(s, meeting_id)
    assert len(keys_first_pass) == 2

    with pytest.raises(ClientError):
        get_object(raw_key)  # objects are really gone
    with pytest.raises(ClientError):
        get_object(norm_key)  # both keys, not just one
    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None, (
            "the simulated crash must leave the row intact, not half-deleted"
        )

    # The "next sweep": a full, ordinary call. Must not raise on the
    # already-missing keys, and must finish the database delete this time.
    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None


def test_delete_object_on_an_already_missing_key_does_not_raise():
    """The idempotency primitive the whole reconciliation story rests on."""
    from meeting_mgr.storage import delete_object

    ensure_bucket()
    key = f"reconciliation/{uuid.uuid4()}"
    delete_object(key)  # never existed -- must not raise


def test_an_audio_crash_after_storage_delete_but_before_db_delete_is_recovered_by_a_second_run():
    """Mirrors the full-purge crash test for purge_meeting_audio, which has a
    different shape: it deletes a Recording row, not the whole Meeting, so
    the crash leaves a different partial state -- objects gone, Recording
    row (and everything else) still present."""
    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        s.add(Segment(meeting_id=meeting_id, start_seconds=0, end_seconds=1, text="hi"))
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    # Step 1 of purge_meeting_audio, run in isolation -- the "crash" happens
    # right after this, before the Recording-row delete that would normally
    # follow it in the same function call.
    with get_org_session(org_id) as s:
        keys_first_pass = _purge_audio_objects(s, meeting_id)
    assert len(keys_first_pass) == 2

    with pytest.raises(ClientError):
        get_object(raw_key)
    with pytest.raises(ClientError):
        get_object(norm_key)
    with get_session() as s:
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is not None, (
            "the simulated crash must leave the Recording row intact, not half-deleted"
        )

    # The "next sweep": a real call. Must not raise on the already-missing
    # keys, and must finish the Recording-row delete this time.
    purge_meeting_audio(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None, "audio purge must never delete the Meeting"
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is None
        assert s.query(Segment).filter_by(meeting_id=meeting_id).count() == 1, (
            "transcript/artifacts must survive an audio purge, crash or no crash"
        )


def test_a_meeting_crashed_mid_full_purge_is_still_selected_by_the_next_sweep():
    """The claim that matters: if a half-purged meeting were not re-selected
    by select_purge_candidates, the next sweep would never run, and the
    meeting would sit half-deleted -- objects gone, rows intact -- forever,
    with nothing to notice. This proves the full-purge shape of that crash
    still leaves the meeting selectable."""
    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        m.created_at = datetime.utcnow() - timedelta(days=100)
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)

    with get_org_session(org_id) as s:
        _purge_audio_objects(s, meeting_id)  # simulated crash: storage gone, rows intact

    with get_session() as s:
        candidates = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "full"


def test_a_meeting_crashed_mid_audio_purge_is_still_selected_by_the_next_sweep():
    """Same claim, audio shape: select_purge_candidates's audio branch INNER
    JOINs Recording and requires raw_key IS NOT NULL. The simulated crash
    here only deletes storage objects -- it never touches the Recording row
    or its raw_key column -- so the join and the filter both still match and
    the meeting remains selectable. If a crash mid-audio-purge somehow left
    the Recording row gone or raw_key nulled without the storage delete
    having truly completed, this would need to fail, and did not."""
    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        m.created_at = datetime.utcnow() - timedelta(days=100)
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)

    with get_org_session(org_id) as s:
        _purge_audio_objects(s, meeting_id)  # simulated crash: storage gone, rows intact

    with get_session() as s:
        candidates = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "audio"
