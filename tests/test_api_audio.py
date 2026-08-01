import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization, Recording
from meeting_mgr.storage import ensure_bucket, put_object


def _account_and_client() -> TestClient:
    email = f"audio-{uuid.uuid4()}@x.com"
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        s.add(Account(organization_id=org.id, email=email, password_hash=hash_password("pw")))
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200
    return c


def _published_with_audio(monkeypatch, data: bytes) -> tuple[TestClient, int]:
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = _account_and_client()
    mid = c.post(
        "/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")}
    ).json()["meeting_id"]
    ensure_bucket()
    key = f"normalized/{mid}.wav"
    put_object(key, data)
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = key
    return c, mid


def test_full_request_returns_whole_object(monkeypatch):
    c, mid = _published_with_audio(monkeypatch, b"0123456789")
    r = c.get(f"/meetings/{mid}/audio")
    assert r.status_code == 200
    assert r.content == b"0123456789"
    assert r.headers["accept-ranges"] == "bytes"


def test_range_request_returns_partial_content(monkeypatch):
    c, mid = _published_with_audio(monkeypatch, b"0123456789")
    r = c.get(f"/meetings/{mid}/audio", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"


def test_open_ended_range_runs_to_the_end(monkeypatch):
    c, mid = _published_with_audio(monkeypatch, b"0123456789")
    r = c.get(f"/meetings/{mid}/audio", headers={"Range": "bytes=7-"})
    assert r.status_code == 206
    assert r.content == b"789"
    assert r.headers["content-range"] == "bytes 7-9/10"


def test_a_range_past_the_end_is_416(monkeypatch):
    c, mid = _published_with_audio(monkeypatch, b"0123456789")
    r = c.get(f"/meetings/{mid}/audio", headers={"Range": "bytes=99-"})
    assert r.status_code == 416


def test_audio_is_streamed_not_buffered(monkeypatch):
    # An hour of 16 kHz mono WAV is ~115 MB. Reading it into memory to serve
    # it is the pattern this project fixed four times in Phase 1.
    #
    # A naive monkeypatch of meeting_mgr.storage.get_object proves nothing:
    # the endpoint never calls that function, it calls storage.open_object,
    # which talks to the boto3 client's own get_object method directly. So
    # instead we spy on the real S3 response body's .read(): if the object
    # is streamed, _chunks() calls stream.read(65536) repeatedly (bounded
    # reads); if it were buffered, open_object would call stream.read() with
    # no size limit (a single unbounded read) before ever returning.
    from meeting_mgr import storage

    c, mid = _published_with_audio(monkeypatch, b"0123456789")

    real_client = storage._client()
    read_calls = []

    class _SpyClient:
        def get_object(self, **kwargs):
            resp = real_client.get_object(**kwargs)
            body = resp["Body"]
            orig_read = body.read

            def spy_read(*args, **kw):
                read_calls.append(args[0] if args else kw.get("amt"))
                return orig_read(*args, **kw)

            body.read = spy_read
            return resp

        def __getattr__(self, name):
            return getattr(real_client, name)

    monkeypatch.setattr(storage, "_client", lambda: _SpyClient())
    r = c.get(f"/meetings/{mid}/audio")
    assert r.content == b"0123456789"
    # No unbounded read() happened on the underlying stream (that would be
    # buffering); every read that did happen was chunk-bounded.
    assert read_calls, "expected the body to be read at least once"
    assert None not in read_calls


def test_missing_audio_is_404(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = _account_and_client()
    mid = c.post(
        "/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")}
    ).json()["meeting_id"]
    assert c.get(f"/meetings/{mid}/audio").status_code == 404


def test_audio_requires_authentication():
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="published")
        s.add(m)
        s.flush()
        mid = m.id
    r = TestClient(app).get(f"/meetings/{mid}/audio")
    assert r.status_code == 401


def test_audio_from_another_organization_is_404():
    # A Meeting with no Recording at all would 404 regardless of who asks
    # ("no normalized audio for this meeting"), which would make this test
    # pass even if the tenancy check were deleted -- vacuously. A real,
    # fetchable normalized object is required so the only thing that can
    # produce the 404 here is authorize() rejecting the cross-org read.
    ensure_bucket()
    with get_session() as s:
        org_a = Organization(name=f"org-a-{uuid.uuid4()}")
        org_b = Organization(name=f"org-b-{uuid.uuid4()}")
        s.add_all([org_a, org_b])
        s.flush()
        acct = Account(
            organization_id=org_a.id,
            email=f"a-{uuid.uuid4()}@x.com",
            password_hash=hash_password("pw"),
        )
        m = Meeting(organization_id=org_b.id, title="t", status="published")
        s.add_all([acct, m])
        s.flush()
        key = f"normalized/{m.id}.wav"
        put_object(key, b"0123456789")
        s.add(Recording(meeting_id=m.id, raw_key=f"raw/{m.id}/a.wav", normalized_key=key))
        email, mid = acct.email, m.id
    c = TestClient(app)
    c.post("/auth/login", json={"email": email, "password": "pw"})
    assert c.get(f"/meetings/{mid}/audio").status_code == 404
