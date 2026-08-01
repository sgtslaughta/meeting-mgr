import io
import json
import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id, role="member") -> str:
    email = f"{role}-{uuid.uuid4()}@x.com"
    with get_session() as s:
        s.add(
            Account(
                organization_id=org_id, email=email, role=role, password_hash=hash_password("pw")
            )
        )
    return email


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def test_auditor_cannot_start_a_capture():
    org_id = _org()
    email = _account(org_id, role="auditor")
    r = _client_as(email).post("/meetings/capture", data={"title": "standup"})
    assert r.status_code == 403


def test_member_can_start_a_capture_in_capturing_status():
    org_id = _org()
    email = _account(org_id, role="member")
    r = _client_as(email).post("/meetings/capture", data={"title": "standup"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "capturing"
    with get_session() as s:
        assert s.get(Meeting, body["meeting_id"]).status == "capturing"


def test_chunks_upload_and_list_in_order():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "standup"}).json()["meeting_id"]

    for seq in (0, 1, 2):
        r = c.put(
            f"/meetings/{meeting_id}/capture/chunks/{seq}",
            files={"chunk": (f"c{seq}.webm", io.BytesIO(b"x" * 10), "audio/webm")},
        )
        assert r.status_code == 200

    r = c.get(f"/meetings/{meeting_id}/capture/chunks")
    assert r.json() == {"seqs": [0, 1, 2]}


def test_auditor_cannot_upload_a_chunk_to_someone_elses_capture():
    org_id = _org()
    member_email = _account(org_id, role="member")
    auditor_email = _account(org_id, role="auditor")
    meeting_id = (
        _client_as(member_email)
        .post("/meetings/capture", data={"title": "standup"})
        .json()["meeting_id"]
    )

    r = _client_as(auditor_email).put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert r.status_code == 403


def test_chunk_upload_to_a_meeting_in_another_organization_is_not_found():
    org_a, org_b = _org(), _org()
    email_a = _account(org_a, role="member")
    email_b = _account(org_b, role="member")
    meeting_id = (
        _client_as(email_a).post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    )

    r = _client_as(email_b).put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert r.status_code == 404


def test_chunk_list_from_another_organization_is_not_found():
    org_a, org_b = _org(), _org()
    email_a = _account(org_a, role="member")
    email_b = _account(org_b, role="member")
    meeting_id = (
        _client_as(email_a).post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    )

    r = _client_as(email_b).get(f"/meetings/{meeting_id}/capture/chunks")
    assert r.status_code == 404


def test_unauthenticated_cannot_start_a_capture():
    r = TestClient(app).post("/meetings/capture", data={"title": "standup"})
    assert r.status_code == 401


def test_unauthenticated_cannot_upload_a_chunk():
    org_id = _org()
    email = _account(org_id, role="member")
    meeting_id = (
        _client_as(email).post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    )

    r = TestClient(app).put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert r.status_code == 401


def test_unauthenticated_cannot_list_chunks():
    org_id = _org()
    email = _account(org_id, role="member")
    meeting_id = (
        _client_as(email).post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    )

    r = TestClient(app).get(f"/meetings/{meeting_id}/capture/chunks")
    assert r.status_code == 401


def test_upload_chunk_streams_without_reading_whole_file(monkeypatch):
    """put_stream must receive an open file handle it can read in chunks,
    never the file's bytes read fully into memory first."""

    class NoReadAll:
        """A file object that fails if anyone calls .read() with no size arg."""

        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self, size=-1):
            assert size != -1, "upload must stream, not read the whole file"
            return self._buf.read(size)

        def seek(self, *a):
            return self._buf.seek(*a)

        def tell(self):
            return self._buf.tell()

        def close(self):
            return self._buf.close()

    from meeting_mgr import storage

    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "standup"}).json()["meeting_id"]

    captured = {}
    real = storage.put_stream

    def spy(key, fileobj):
        captured["key"] = key
        captured["is_bytes"] = isinstance(fileobj, (bytes, bytearray))
        return real(key, NoReadAll(fileobj.read()))

    monkeypatch.setattr("meeting_mgr.api.capture.put_stream", spy)

    r = c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"AUDIO-BYTES"), "audio/webm")},
    )

    assert r.status_code == 200
    assert captured["is_bytes"] is False
    assert captured["key"] == f"raw/{meeting_id}/chunks/000000.webm"
    assert storage.get_object(captured["key"]) == b"AUDIO-BYTES"


def test_finish_with_no_chunks_is_rejected():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 422


def test_finish_builds_a_manifest_and_enqueues_the_pipeline(monkeypatch):
    from meeting_mgr.api import capture

    enqueued = []
    monkeypatch.setattr(capture, "run_pipeline", lambda meeting_id: enqueued.append(meeting_id))

    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    for seq in (0, 1):
        c.put(
            f"/meetings/{meeting_id}/capture/chunks/{seq}",
            files={"chunk": (f"c{seq}.webm", io.BytesIO(b"x"), "audio/webm")},
        )

    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 200
    assert r.json()["status"] == "pending"

    with get_session() as s:
        from meeting_mgr.models import Recording

        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        assert rec.raw_key.startswith("manifest:")
        assert s.get(Meeting, meeting_id).status == "pending"
    assert enqueued == [meeting_id]


def test_finish_twice_is_rejected_with_409():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert c.post(f"/meetings/{meeting_id}/capture/finish").status_code == 200
    assert c.post(f"/meetings/{meeting_id}/capture/finish").status_code == 409


def test_chunk_upload_after_finish_is_rejected_with_409():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    c.post(f"/meetings/{meeting_id}/capture/finish")

    r = c.put(
        f"/meetings/{meeting_id}/capture/chunks/1",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert r.status_code == 409


def test_finish_orders_the_manifest_numerically_not_lexicographically():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    for seq in range(11):
        r = c.put(
            f"/meetings/{meeting_id}/capture/chunks/{seq}",
            files={"chunk": (f"c{seq}.webm", io.BytesIO(bytes([seq])), "audio/webm")},
        )
        assert r.status_code == 200

    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 200

    with get_session() as s:
        from meeting_mgr.models import Recording

        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        manifest_key = rec.raw_key.removeprefix("manifest:")

    from meeting_mgr import storage

    keys = json.loads(storage.get_object(manifest_key))
    seqs = [int(k.removeprefix(f"raw/{meeting_id}/chunks/").removesuffix(".webm")) for k in keys]
    assert seqs == list(range(11))


def test_finish_accepts_a_gap_in_the_sequence():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    for seq in (1, 2, 4):
        r = c.put(
            f"/meetings/{meeting_id}/capture/chunks/{seq}",
            files={"chunk": (f"c{seq}.webm", io.BytesIO(b"x"), "audio/webm")},
        )
        assert r.status_code == 200

    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 200

    with get_session() as s:
        from meeting_mgr.models import Recording

        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        manifest_key = rec.raw_key.removeprefix("manifest:")

    from meeting_mgr import storage

    keys = json.loads(storage.get_object(manifest_key))
    seqs = [int(k.removeprefix(f"raw/{meeting_id}/chunks/").removesuffix(".webm")) for k in keys]
    assert seqs == [1, 2, 4]


def test_finish_requires_authentication():
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )

    r = TestClient(app).post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 401


def test_auditor_cannot_finish_a_capture():
    org_id = _org()
    member_email = _account(org_id, role="member")
    auditor_email = _account(org_id, role="auditor")
    c = _client_as(member_email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )

    r = _client_as(auditor_email).post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 403


def test_finish_from_another_organization_is_not_found():
    org_a, org_b = _org(), _org()
    email_a = _account(org_a, role="member")
    email_b = _account(org_b, role="member")
    c = _client_as(email_a)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c.webm", io.BytesIO(b"x"), "audio/webm")},
    )

    r = _client_as(email_b).post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 404


def test_finish_toctou_a_straggler_upload_after_the_status_flip_is_rejected_not_orphaned(
    monkeypatch,
):
    """Simulates the interleaving pass 1 flagged: a chunk upload racing
    finish_capture(). The hook fires exactly between finish_capture's status
    flip and its list_keys() snapshot, and issues a real chunk PUT through
    the same authenticated client -- i.e. a straggler whose own status
    recheck is happening right now, concurrently with finish.

    Kill: this pins the fix's actual mechanism, not just its outcome. Revert
    finish_capture to flip `m.status = "pending"` only at the end (after
    building/uploading the manifest), as it did before this fix -- putting
    the flip back where it was -- and the straggler's status recheck reads
    "capturing" again, so its PUT succeeds (200) while the manifest snapshot
    (taken moments earlier) already excluded it: straggler_status == 200
    with the chunk missing from the manifest, which the assertion below
    forbids and the final assert (409) directly contradicts.
    """
    from meeting_mgr.api import capture

    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]
    c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c0.webm", io.BytesIO(b"x"), "audio/webm")},
    )

    result = {}

    def race(mid):
        r = c.put(
            f"/meetings/{mid}/capture/chunks/1",
            files={"chunk": ("c1.webm", io.BytesIO(b"y"), "audio/webm")},
        )
        result["status"] = r.status_code

    monkeypatch.setattr(capture, "_finish_race_hook", race)

    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 200

    with get_session() as s:
        from meeting_mgr.models import Recording

        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        manifest_key = rec.raw_key.removeprefix("manifest:")
    from meeting_mgr import storage

    keys = json.loads(storage.get_object(manifest_key))
    straggler_key = capture._chunk_key(meeting_id, 1)
    straggler_included = straggler_key in keys

    # The invariant the fix establishes: a straggler that lands in this
    # window is either rejected outright (409, nothing orphaned) or, if
    # ever accepted, must be present in the manifest -- never both accepted
    # and silently excluded (the data-loss shape pass 1 found).
    assert not (result["status"] == 200 and not straggler_included)
    # The actual mechanism: the flip already committed, so the straggler's
    # own status recheck is rejected before it ever writes to S3.
    assert result["status"] == 409


def test_finish_reverts_to_capturing_when_no_chunks_survive_the_flip():
    """The empty-manifest 422 path (test_finish_with_no_chunks_is_rejected)
    must leave the Meeting resumable, not stuck in the intermediate
    "finishing" status the TOCTOU fix introduces.

    Kill: dropping the `m.status = "capturing"` revert (leaving the Meeting
    at "finishing" on the empty path) turns this red.
    """
    org_id = _org()
    email = _account(org_id, role="member")
    c = _client_as(email)
    meeting_id = c.post("/meetings/capture", data={"title": "s"}).json()["meeting_id"]

    r = c.post(f"/meetings/{meeting_id}/capture/finish")
    assert r.status_code == 422

    with get_session() as s:
        assert s.get(Meeting, meeting_id).status == "capturing"

    # And proves it's actually resumable, not just cosmetically reverted.
    r = c.put(
        f"/meetings/{meeting_id}/capture/chunks/0",
        files={"chunk": ("c0.webm", io.BytesIO(b"x"), "audio/webm")},
    )
    assert r.status_code == 200
