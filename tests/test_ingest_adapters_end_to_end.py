import io
import os
import time
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization, Recording, WatchFolder
from meeting_mgr.pipeline.watch import scan_watch_folder
from meeting_mgr.retention import select_purge_candidates, upsert_policy
from meeting_mgr.storage import ensure_bucket, get_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _admin(org_id) -> tuple[str, int]:
    email = f"admin-{uuid.uuid4()}@x.com"
    with get_session() as s:
        a = Account(
            organization_id=org_id, email=email, role="admin", password_hash=hash_password("pw")
        )
        s.add(a)
        s.flush()
        return email, a.id


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def test_watch_folder_meeting_is_a_purge_candidate_like_any_other(tmp_path, monkeypatch):
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    org_id = _org()
    _, admin_id = _admin(org_id)
    with get_session() as s:
        wf = WatchFolder(organization_id=org_id, owner_account_id=admin_id, root_path=str(tmp_path))
        s.add(wf)
        s.flush()
        wf_id = wf.id
    f = tmp_path / "rec.wav"
    f.write_bytes(b"x")
    old = time.time() - 60
    os.utime(f, (old, old))

    scan_watch_folder.run(wf_id, org_id)

    with get_session() as s:
        meeting_id = s.query(Meeting).filter_by(organization_id=org_id).one().id
        # Backdate created_at the same way test_retention_candidates.py does,
        # to make it purge-eligible without waiting real days.
        s.get(Meeting, meeting_id).created_at = datetime.utcnow() - timedelta(days=400)

    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "full"


def test_browser_capture_meeting_is_a_purge_candidate_like_any_other():
    org_id = _org()
    admin_email, _ = _admin(org_id)
    c = _client_as(admin_email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    c.post(f"/meetings/{meeting_id}/capture/finish")

    with get_session() as s:
        s.get(Meeting, meeting_id).created_at = datetime.utcnow() - timedelta(days=400)

    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in candidates] == [meeting_id]


def test_purge_of_a_capture_meeting_deletes_the_row_but_leaks_the_chunks_KNOWN_GAP():
    """Documents a known Phase 4/5 interaction, out of scope for this task
    (see Task 10's review and the Task 13 brief): purge.py's
    _purge_audio_objects() passes rec.raw_key straight to delete_object().
    For a browser-capture Meeting, raw_key is "manifest:raw/{id}/manifest.json"
    -- not a real object key -- so delete_object() is a silent no-op and the
    manifest object plus every chunk survive a "purge". The Meeting/Recording
    *rows* are still correctly deleted; only the bytes leak.

    This test pins the CURRENT (wrong) behaviour deliberately, so it is not
    silently fixed as a side effect of unrelated work. When purge.py is
    corrected to reconstruct chunk keys from the manifest before deleting,
    this test should be updated to assert the objects ARE gone -- flip the
    final two assertions rather than deleting this test outright.
    """
    org_id = _org()
    admin_email, _ = _admin(org_id)
    c = _client_as(admin_email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    chunk_key = f"raw/{meeting_id}/chunks/000000.webm"
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    c.post(f"/meetings/{meeting_id}/capture/finish")

    with get_session() as s:
        manifest_key = s.query(Recording).filter_by(meeting_id=meeting_id).one().raw_key
        manifest_key = manifest_key.removeprefix("manifest:")

    from meeting_mgr.pipeline.purge import purge_meeting_full

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None, "the Meeting row is correctly deleted"
        assert s.query(Recording).filter_by(meeting_id=meeting_id).count() == 0, (
            "the Recording row is correctly deleted"
        )
    # The bytes, however, are NOT deleted -- this is the leak.
    assert get_object(manifest_key) is not None
    assert get_object(chunk_key) == b"x"
