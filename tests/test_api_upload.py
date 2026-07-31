import io

from fastapi.testclient import TestClient
from meeting_mgr.api.main import app
from meeting_mgr.db import get_session
from meeting_mgr.models import ActionItem
from meeting_mgr.storage import get_object

def test_upload_creates_meeting_and_stores_recording(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    r = c.post("/meetings", data={"title": "standup"},
               files={"file": ("a.m4a", b"AUDIO", "audio/mp4")})
    assert r.status_code == 201
    mid = r.json()["meeting_id"]
    assert get_object(f"raw/{mid}/a.m4a") == b"AUDIO"


def test_upload_streams_without_reading_whole_file(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)

    class NoReadAll:
        """A file object that fails if anyone calls .read() with no size arg."""
        def __init__(self, data): self._buf = io.BytesIO(data)
        def read(self, size=-1):
            assert size != -1, "upload must stream, not read the whole file"
            return self._buf.read(size)
        def seek(self, *a): return self._buf.seek(*a)
        def tell(self): return self._buf.tell()
        def close(self): return self._buf.close()

    from meeting_mgr import storage
    captured = {}
    real = storage.put_stream
    def spy(key, fileobj):
        captured["key"] = key
        return real(key, NoReadAll(fileobj.read()))
    monkeypatch.setattr("meeting_mgr.api.meetings.put_stream", spy)

    c = TestClient(app)
    r = c.post("/meetings", data={"title": "standup"},
               files={"file": ("a.m4a", b"AUDIO-BYTES", "audio/mp4")})
    assert r.status_code == 201
    assert captured["key"] == f"raw/{r.json()['meeting_id']}/a.m4a"
    assert get_object(captured["key"]) == b"AUDIO-BYTES"


def test_read_meeting_exposes_only_allowlisted_fields(monkeypatch):
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", lambda mid: None)
    c = TestClient(app)
    mid = c.post("/meetings", data={"title": "standup"},
                 files={"file": ("a.m4a", b"AUDIO", "audio/mp4")}).json()["meeting_id"]

    with get_session() as s:
        s.add(ActionItem(meeting_id=mid, text="ship it", citations=[],
                         provenance="inferred"))

    body = c.get(f"/meetings/{mid}").json()
    assert set(body["action_items"][0]) == {
        "id", "text", "participant_id", "due_date", "status",
        "citations", "provenance",
    }
