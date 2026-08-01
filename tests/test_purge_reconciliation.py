import uuid

import pytest
from botocore.exceptions import ClientError

from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting, Organization, Recording
from meeting_mgr.pipeline.purge import _purge_audio_objects, purge_meeting_full
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
    from meeting_mgr.db import get_org_session

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
