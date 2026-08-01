import uuid
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from meeting_mgr.db import get_session
from meeting_mgr.models import (
    ActionItem,
    Attribution,
    AuditLogEntry,
    DecisionPoint,
    KeyTopic,
    Meeting,
    Minute,
    Organization,
    Recording,
    Segment,
    SpeakerCluster,
)
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
        put_object(raw_key, b"raw-bytes")
        put_object(norm_key, b"norm-bytes")
        s.add(Recording(meeting_id=m.id, raw_key=raw_key, normalized_key=norm_key))
        s.add(Segment(meeting_id=m.id, start_seconds=0, end_seconds=1, text="hello"))
        return m.id


def test_purge_meeting_audio_deletes_the_objects_and_the_recording_row():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    purge_meeting_audio(org_id, meeting_id)

    with pytest.raises(ClientError):
        get_object(raw_key)
    with pytest.raises(ClientError):
        get_object(norm_key)
    with get_session() as s:
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is None


def test_purge_meeting_audio_leaves_the_transcript_and_meeting_intact():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)

    purge_meeting_audio(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None
        assert s.query(Segment).filter_by(meeting_id=meeting_id).count() == 1


def test_purge_meeting_audio_records_an_audit_entry_with_no_content():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)

    purge_meeting_audio(org_id, meeting_id)

    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.audio")
            .one()
        )
        assert entry.target == f"meeting:{meeting_id}"
        assert entry.actor_account_id is None, "an automated purge has no human actor"
        assert entry.detail == {"keys_deleted": 2}


def test_purge_meeting_audio_on_a_meeting_with_no_recording_is_a_silent_no_op():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    with get_session() as s:
        m = Meeting(organization_id=org_id, title="no-audio")
        s.add(m)
        s.flush()
        meeting_id = m.id

    purge_meeting_audio(org_id, meeting_id)  # must not raise

    with get_session() as s:
        assert s.query(AuditLogEntry).filter_by(organization_id=org_id).count() == 0


def test_purge_meeting_audio_cannot_reach_a_meeting_in_another_organization():
    """A meeting_id from the wrong org must be invisible under this org's
    get_org_session -- proves the RLS backstop, not just app-layer scoping."""
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_a, org_b = _org(), _org()
    meeting_id = _meeting_with_audio(org_b)

    purge_meeting_audio(org_a, meeting_id)  # wrong org: must be a no-op

    with get_session() as s:
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is not None, (
            "purge scoped to org_a must not delete org_b's Recording"
        )


def _meeting_with_full_artifacts(org_id: int) -> int:
    """A meeting with a Recording plus one row of every derived-artifact
    type, so a purge test can assert EXACT survivor counts rather than
    'not empty'."""
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        s.add(KeyTopic(meeting_id=meeting_id, title="topic", citations=[1], provenance="confirmed"))
        s.add(Minute(meeting_id=meeting_id, text="minute text", citations=[1]))
        s.add(ActionItem(meeting_id=meeting_id, text="do the thing", citations=[1]))
        s.add(DecisionPoint(meeting_id=meeting_id, text="decided", citations=[1]))
        cluster = SpeakerCluster(meeting_id=meeting_id, label="SPEAKER_00")
        s.add(cluster)
        s.flush()
        s.add(
            Attribution(
                cluster_id=cluster.id,
                participant_id=_participant(s, org_id),
                provenance="confirmed",
            )
        )
    return meeting_id


def _participant(s, org_id: int) -> int:
    from meeting_mgr.models import Participant

    p = Participant(organization_id=org_id, name=f"p-{uuid.uuid4()}")
    s.add(p)
    s.flush()
    return p.id


def test_purge_meeting_audio_leaves_every_derived_artifact_type_intact_with_exact_counts():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_full_artifacts(org_id)

    purge_meeting_audio(org_id, meeting_id)

    with get_session() as s:
        assert s.query(Segment).filter_by(meeting_id=meeting_id).count() == 1
        assert s.query(KeyTopic).filter_by(meeting_id=meeting_id).count() == 1
        assert s.query(Minute).filter_by(meeting_id=meeting_id).count() == 1
        assert s.query(ActionItem).filter_by(meeting_id=meeting_id).count() == 1
        assert s.query(DecisionPoint).filter_by(meeting_id=meeting_id).count() == 1
        assert s.query(SpeakerCluster).filter_by(meeting_id=meeting_id).count() == 1
        cluster_ids = [c.id for c in s.query(SpeakerCluster).filter_by(meeting_id=meeting_id).all()]
        assert s.query(Attribution).filter(Attribution.cluster_id.in_(cluster_ids)).count() == 1
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is None


def test_purge_meeting_audio_deletes_the_exact_storage_keys(monkeypatch):
    import meeting_mgr.pipeline.purge as purge_mod

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    deleted_keys: list[str] = []
    real_delete = purge_mod.delete_object

    def spy(key: str) -> None:
        deleted_keys.append(key)
        real_delete(key)

    monkeypatch.setattr(purge_mod, "delete_object", spy)

    purge_mod.purge_meeting_audio(org_id, meeting_id)

    assert sorted(deleted_keys) == sorted([raw_key, norm_key])


def test_purge_meeting_audio_closes_the_loop_meeting_no_longer_an_audio_candidate():
    """After the purge, the meeting must not resurface via
    select_purge_candidates -- the exact bug Task 4 flagged if raw_key (or,
    here, the whole Recording row) is not cleared."""
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)
    with get_session() as s:
        # Backdate so it is old enough to be an audio candidate.
        m = s.get(Meeting, meeting_id)
        m.created_at = datetime.utcnow() - timedelta(days=100)
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)

    with get_session() as s:
        before = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in before] == [meeting_id]

    purge_meeting_audio(org_id, meeting_id)

    with get_session() as s:
        after = select_purge_candidates(s, org_id)
    assert after == []


def test_purge_meeting_audio_is_a_no_op_when_run_twice():
    from meeting_mgr.pipeline.purge import purge_meeting_audio

    org_id = _org()
    meeting_id = _meeting_with_audio(org_id)

    purge_meeting_audio(org_id, meeting_id)
    purge_meeting_audio(org_id, meeting_id)  # must not raise

    with get_session() as s:
        assert s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none() is None
        assert (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.audio")
            .count()
            == 1
        ), "the second no-op run must not write a second audit entry"
