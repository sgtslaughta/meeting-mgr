from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.db import get_session
from meeting_mgr.models import Recording
from meeting_mgr.storage import ensure_bucket, put_object


def _published_with_audio(monkeypatch, data: bytes) -> int:
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    mid = c.post(
        "/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")}
    ).json()["meeting_id"]
    ensure_bucket()
    key = f"normalized/{mid}.wav"
    put_object(key, data)
    with get_session() as s:
        s.query(Recording).filter_by(meeting_id=mid).one().normalized_key = key
    return mid


def test_full_request_returns_whole_object(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio")
    assert r.status_code == 200
    assert r.content == b"0123456789"
    assert r.headers["accept-ranges"] == "bytes"


def test_range_request_returns_partial_content(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == b"2345"
    assert r.headers["content-range"] == "bytes 2-5/10"


def test_open_ended_range_runs_to_the_end(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio", headers={"Range": "bytes=7-"})
    assert r.status_code == 206
    assert r.content == b"789"
    assert r.headers["content-range"] == "bytes 7-9/10"


def test_a_range_past_the_end_is_416(monkeypatch):
    mid = _published_with_audio(monkeypatch, b"0123456789")
    r = TestClient(app).get(f"/meetings/{mid}/audio", headers={"Range": "bytes=99-"})
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

    mid = _published_with_audio(monkeypatch, b"0123456789")

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
    r = TestClient(app).get(f"/meetings/{mid}/audio")
    assert r.content == b"0123456789"
    # No unbounded read() happened on the underlying stream (that would be
    # buffering); every read that did happen was chunk-bounded.
    assert read_calls, "expected the body to be read at least once"
    assert None not in read_calls


def test_missing_audio_is_404(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    mid = c.post(
        "/meetings", data={"title": "t"}, files={"file": ("a.wav", b"raw", "audio/wav")}
    ).json()["meeting_id"]
    assert c.get(f"/meetings/{mid}/audio").status_code == 404
