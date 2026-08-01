import uuid

import pytest
from botocore.exceptions import ClientError

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
from meeting_mgr.storage import ensure_bucket, get_object, put_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _full_meeting(org_id: int) -> tuple[int, int]:
    """A Meeting with audio, a Segment, a SpeakerCluster+Attribution, and an
    ActionItem -- one row in every child table purge_meeting_full must
    remove, plus a Participant it must NOT remove."""
    ensure_bucket()
    with get_session() as s:
        p = Participant(organization_id=org_id, name=f"p-{uuid.uuid4()}")
        s.add(p)
        s.flush()
        participant_id = p.id

        m = Meeting(organization_id=org_id, title="t")
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
        s.add(
            Attribution(
                cluster_id=cluster.id, participant_id=participant_id, provenance="confirmed"
            )
        )
        s.add(ActionItem(meeting_id=m.id, text="do it", citations=[1], provenance="confirmed"))
        return m.id, participant_id


def test_purge_meeting_full_removes_the_meeting_and_every_child_row():
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, participant_id = _full_meeting(org_id)

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        assert s.query(Recording).filter_by(meeting_id=meeting_id).count() == 0
        assert s.query(Segment).filter_by(meeting_id=meeting_id).count() == 0
        assert s.query(SpeakerCluster).filter_by(meeting_id=meeting_id).count() == 0
        assert s.query(ActionItem).filter_by(meeting_id=meeting_id).count() == 0
        assert (
            s.query(Attribution)
            .join(SpeakerCluster, Attribution.cluster_id == SpeakerCluster.id, isouter=True)
            .filter(SpeakerCluster.meeting_id == meeting_id)
            .count()
            == 0
        ), "attribution must be gone even though it has no meeting_id column of its own"
        assert s.get(Participant, participant_id) is not None, (
            "Participant is organization-scoped and must survive a Meeting purge"
        )


def test_purge_meeting_full_deletes_the_audio_objects():
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    purge_meeting_full(org_id, meeting_id)

    with pytest.raises(ClientError):
        get_object(raw_key)
    with pytest.raises(ClientError):
        get_object(norm_key)


def test_purge_meeting_full_does_not_special_case_confirmed_provenance():
    """The Attribution and ActionItem seeded above are both provenance
    'confirmed' -- purge_meeting_full must delete them exactly like an
    inferred one. See Global Constraints: confirmation is not a deletion
    exemption."""
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)
    with get_session() as s:
        assert (
            s.query(ActionItem).filter_by(meeting_id=meeting_id, provenance="confirmed").count()
            == 1
        )

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.query(ActionItem).filter_by(meeting_id=meeting_id).count() == 0


def test_purge_meeting_full_records_an_audit_entry_that_survives_the_meeting():
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.full")
            .one()
        )
        assert entry.target == f"meeting:{meeting_id}"
        assert entry.actor_account_id is None


def test_purge_meeting_full_cannot_reach_a_meeting_in_another_organization():
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_a, org_b = _org(), _org()
    meeting_id, _ = _full_meeting(org_b)

    purge_meeting_full(org_a, meeting_id)  # wrong org

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None, (
            "purge scoped to org_a must not delete org_b's Meeting"
        )


def test_purge_meeting_full_is_a_no_op_when_run_twice():
    """The sweep re-selecting a crash-interrupted purge must not raise on an
    already-purged meeting, and must not write a second audit entry."""
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)

    purge_meeting_full(org_id, meeting_id)
    purge_meeting_full(org_id, meeting_id)  # must not raise

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        assert (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.full")
            .count()
            == 1
        ), "the second no-op run must not write a second audit entry"


def test_purge_meeting_full_deletes_the_exact_storage_keys(monkeypatch):
    import meeting_mgr.pipeline.purge as purge_mod

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key, norm_key = rec.raw_key, rec.normalized_key

    deleted_keys: list[str] = []
    real_delete = purge_mod.delete_object

    def spy(key: str) -> None:
        deleted_keys.append(key)
        real_delete(key)

    monkeypatch.setattr(purge_mod, "delete_object", spy)

    purge_mod.purge_meeting_full(org_id, meeting_id)

    assert sorted(deleted_keys) == sorted([raw_key, norm_key])


def test_purge_meeting_full_audit_entry_carries_no_content():
    """The audit detail may carry ids and counts, never transcript text,
    participant names, or embeddings -- see Global Constraints."""
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.full")
            .one()
        )
        assert entry.detail == {"keys_deleted": 2}


def test_purge_meeting_full_audit_entries_survive_the_purged_meeting():
    """audit_log_entry is org-scoped with no FK to meeting -- a purged
    meeting's audit trail must remain queryable afterward."""
    from meeting_mgr.pipeline.purge import purge_meeting_full

    org_id = _org()
    meeting_id, _ = _full_meeting(org_id)

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        assert (
            s.query(AuditLogEntry)
            .filter_by(
                organization_id=org_id,
                action="meeting.purge.full",
                target=f"meeting:{meeting_id}",
            )
            .count()
            == 1
        )
