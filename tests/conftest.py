import pytest
from fake_inference import FakeInference

from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting, Organization, Recording
from meeting_mgr.pipeline.app import celery_app
from meeting_mgr.storage import ensure_bucket, put_object

# No worker process consumes the test Redis broker, so a genuine .delay()
# would enqueue and never run. Eager mode makes .delay() execute the task
# body synchronously in-process — production still goes through the real
# broker and a worker, this only affects the test session.
celery_app.conf.task_always_eager = True


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
            s.add(m)
            s.flush()
            key = f"raw/{m.id}/{name}"
            put_object(key, raw)
            s.add(Recording(meeting_id=m.id, raw_key=key))
            return m.id

    return _make
