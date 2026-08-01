import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import (
    Account,
    Attribution,
    Meeting,
    Organization,
    Participant,
    SpeakerCluster,
)
from meeting_mgr.participants import resolve_participant


def _client_as(email: str, password: str = "pw") -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": password}).status_code == 200
    return c


def _meeting_with_cluster() -> tuple[int, int, TestClient]:
    org = Organization(name=f"org-{uuid.uuid4()}")
    with get_session() as s:
        s.add(org)
        s.flush()
        email = f"member-{uuid.uuid4()}@x.com"
        owner = Account(
            organization_id=org.id, email=email, role="member", password_hash=hash_password("pw")
        )
        s.add(owner)
        s.flush()
        m = Meeting(
            organization_id=org.id,
            title="t",
            status="published",
            owner_account_id=owner.id,
            visibility="organization",
        )
        s.add(m)
        s.flush()
        c = SpeakerCluster(meeting_id=m.id, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        mid, cid = m.id, c.id
    return mid, cid, _client_as(email)


def test_confirming_a_name_writes_a_confirmed_attribution():
    mid, cid, client = _meeting_with_cluster()
    r = client.patch(f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"})
    assert r.status_code == 200
    assert r.json()["provenance"] == "confirmed"
    assert r.json()["participant_name"] == "Sarah"
    with get_session() as s:
        a = s.query(Attribution).join(SpeakerCluster).filter(SpeakerCluster.meeting_id == mid).one()
        assert a.provenance == "confirmed"
        assert s.get(Participant, a.participant_id).name == "Sarah"


def test_correcting_an_inferred_attribution_replaces_it():
    mid, cid, client = _meeting_with_cluster()
    with get_session() as s:
        org_id = s.get(Meeting, mid).organization_id
        # Reuse the shared resolver rather than inserting a bare Participant:
        # the suite reruns against a persistent DB with no cleanup fixture,
        # so a hardcoded name would collide with itself on the second pass.
        p_id = resolve_participant(s, org_id, "Wrong Guess")
        s.add(Attribution(cluster_id=cid, participant_id=p_id, provenance="inferred"))
    client.patch(f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"})
    with get_session() as s:
        rows = (
            s.query(Attribution).join(SpeakerCluster).filter(SpeakerCluster.meeting_id == mid).all()
        )
        assert len(rows) == 1, "correcting must replace, not accumulate"
        assert s.get(Participant, rows[0].participant_id).name == "Sarah"
        assert rows[0].provenance == "confirmed"


def test_null_name_clears_the_attribution():
    mid, cid, client = _meeting_with_cluster()
    client.patch(f"/meetings/{mid}/clusters/{cid}", json={"participant_name": "Sarah"})
    r = client.patch(f"/meetings/{mid}/clusters/{cid}", json={"participant_name": None})
    assert r.status_code == 200
    assert r.json()["participant_id"] is None
    with get_session() as s:
        assert (
            s.query(Attribution)
            .join(SpeakerCluster)
            .filter(SpeakerCluster.meeting_id == mid)
            .count()
        ) == 0


def test_cluster_from_another_meeting_is_404():
    mid_a, _, client = _meeting_with_cluster()
    _, cid_b, _ = _meeting_with_cluster()
    r = client.patch(f"/meetings/{mid_a}/clusters/{cid_b}", json={"participant_name": "Sarah"})
    assert r.status_code == 404
