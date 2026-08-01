import uuid

from sqlalchemy import text

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Meeting, Organization, Segment, SpeakerCluster


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _meeting(org_id: int, title: str) -> int:
    with get_session() as s:
        m = Meeting(organization_id=org_id, title=title)
        s.add(m)
        s.flush()
        return m.id


def test_segment_tenant_isolation():
    """Direct meeting_id child table: a raw SELECT with no app-layer filter
    must not cross tenants, proven via the meeting-subquery policy alone."""
    org_a, org_b = _org(), _org()
    meeting_a = _meeting(org_a, "a")
    meeting_b = _meeting(org_b, "b")

    with get_session() as s:
        s.add(Segment(meeting_id=meeting_a, start_seconds=0, end_seconds=1, text="visible-a"))
        s.add(Segment(meeting_id=meeting_b, start_seconds=0, end_seconds=1, text="secret-b"))

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT text FROM segment")).fetchall()
        texts = {r[0] for r in rows}
    assert "visible-a" in texts
    assert "secret-b" not in texts, "RLS did not confine segment to its own organization"


def test_recording_tenant_isolation():
    org_a, org_b = _org(), _org()
    meeting_a = _meeting(org_a, "a")
    meeting_b = _meeting(org_b, "b")

    with get_session() as s:
        s.execute(
            text("INSERT INTO recording (meeting_id, raw_key) VALUES (:m, :k)"),
            {"m": meeting_a, "k": "visible-a-key"},
        )
        s.execute(
            text("INSERT INTO recording (meeting_id, raw_key) VALUES (:m, :k)"),
            {"m": meeting_b, "k": "secret-b-key"},
        )

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT raw_key FROM recording")).fetchall()
        keys = {r[0] for r in rows}
    assert "visible-a-key" in keys
    assert "secret-b-key" not in keys, "RLS did not confine recording to its own organization"


def test_key_topic_tenant_isolation():
    org_a, org_b = _org(), _org()
    meeting_a = _meeting(org_a, "a")
    meeting_b = _meeting(org_b, "b")

    with get_session() as s:
        s.execute(
            text(
                "INSERT INTO key_topic (meeting_id, title, citations, provenance) "
                "VALUES (:m, :t, '[]', 'inferred')"
            ),
            {"m": meeting_a, "t": "visible-a-topic"},
        )
        s.execute(
            text(
                "INSERT INTO key_topic (meeting_id, title, citations, provenance) "
                "VALUES (:m, :t, '[]', 'inferred')"
            ),
            {"m": meeting_b, "t": "secret-b-topic"},
        )

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT title FROM key_topic")).fetchall()
        titles = {r[0] for r in rows}
    assert "visible-a-topic" in titles
    assert "secret-b-topic" not in titles, "RLS did not confine key_topic to its own organization"


def test_attribution_tenant_isolation_via_speaker_cluster():
    """attribution has no meeting_id of its own; it reaches meeting only via
    speaker_cluster.meeting_id, so this proves the two-level chain."""
    org_a, org_b = _org(), _org()
    meeting_a = _meeting(org_a, "a")
    meeting_b = _meeting(org_b, "b")

    with get_session() as s:
        cluster_a = SpeakerCluster(meeting_id=meeting_a, label="SPEAKER_00")
        cluster_b = SpeakerCluster(meeting_id=meeting_b, label="SPEAKER_00")
        s.add(cluster_a)
        s.add(cluster_b)
        s.flush()
        cluster_a_id, cluster_b_id = cluster_a.id, cluster_b.id

        p_a = s.execute(
            text("INSERT INTO participant (organization_id, name) VALUES (:o, :n) RETURNING id"),
            {"o": org_a, "n": f"participant-a-{uuid.uuid4()}"},
        ).scalar_one()
        p_b = s.execute(
            text("INSERT INTO participant (organization_id, name) VALUES (:o, :n) RETURNING id"),
            {"o": org_b, "n": f"participant-b-{uuid.uuid4()}"},
        ).scalar_one()

        s.execute(
            text(
                "INSERT INTO attribution (cluster_id, participant_id, provenance) "
                "VALUES (:c, :p, 'confirmed')"
            ),
            {"c": cluster_a_id, "p": p_a},
        )
        s.execute(
            text(
                "INSERT INTO attribution (cluster_id, participant_id, provenance) "
                "VALUES (:c, :p, 'confirmed')"
            ),
            {"c": cluster_b_id, "p": p_b},
        )

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT cluster_id FROM attribution")).fetchall()
        seen = {r[0] for r in rows}
    assert cluster_a_id in seen
    assert cluster_b_id not in seen, "RLS did not confine attribution to its own organization"
