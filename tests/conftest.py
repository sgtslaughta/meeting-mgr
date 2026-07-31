import pytest
from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting, Organization, Recording
from meeting_mgr.storage import ensure_bucket, put_object
from fake_inference import FakeInference

@pytest.fixture
def fake_inference():
    f = FakeInference()
    yield f
    f.stop()

@pytest.fixture
def make_meeting():
    """Factory: create a Meeting with a stored raw Recording, return its id."""
    def _make(raw: bytes, name: str = "a.wav") -> int:
        ensure_bucket()
        with get_session() as s:
            org = s.query(Organization).filter_by(name="default").one()
            m = Meeting(organization_id=org.id, title="t", status="pending")
            s.add(m); s.flush()
            key = f"raw/{m.id}/{name}"
            put_object(key, raw)
            s.add(Recording(meeting_id=m.id, raw_key=key))
            return m.id
    return _make
