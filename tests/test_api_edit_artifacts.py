import pytest
from fastapi.testclient import TestClient
from meeting_mgr.api import edits
from meeting_mgr.api.main import app
from meeting_mgr.db import get_session
from meeting_mgr.models import (ActionItem, DecisionPoint, KeyTopic, Meeting,
                                Organization)

def _meeting_with_topic() -> tuple[int, int]:
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="published")
        s.add(m); s.flush()
        t = KeyTopic(meeting_id=m.id, title="budget", citations=[1],
                     provenance="inferred")
        s.add(t); s.flush()
        return m.id, t.id

def test_editing_promotes_provenance_to_confirmed():
    mid, tid = _meeting_with_topic()
    r = TestClient(app).patch(f"/meetings/{mid}/key_topics/{tid}",
                              json={"title": "budget and hiring"})
    assert r.status_code == 200
    assert r.json()["title"] == "budget and hiring"
    assert r.json()["provenance"] == "confirmed"
    with get_session() as s:
        assert s.get(KeyTopic, tid).provenance == "confirmed"

def test_editing_leaves_citations_untouched():
    mid, tid = _meeting_with_topic()
    TestClient(app).patch(f"/meetings/{mid}/key_topics/{tid}",
                          json={"title": "renamed"})
    with get_session() as s:
        assert s.get(KeyTopic, tid).citations == [1], \
            "a human rewording a claim does not change which segments it came from"

def test_unknown_field_is_rejected():
    mid, tid = _meeting_with_topic()
    r = TestClient(app).patch(f"/meetings/{mid}/key_topics/{tid}",
                              json={"provenance": "confirmed", "title": "x"})
    assert r.status_code == 400, "provenance is not client-writable"

def test_item_from_another_meeting_is_404():
    mid_a, _ = _meeting_with_topic()
    _, tid_b = _meeting_with_topic()
    r = TestClient(app).patch(f"/meetings/{mid_a}/key_topics/{tid_b}",
                              json={"title": "x"})
    assert r.status_code == 404

def test_delete_removes_the_item():
    mid, tid = _meeting_with_topic()
    assert TestClient(app).delete(f"/meetings/{mid}/key_topics/{tid}").status_code == 204
    with get_session() as s:
        assert s.get(KeyTopic, tid) is None

def test_regenerate_replaces_only_that_artifact_type(monkeypatch):
    mid, tid = _meeting_with_topic()
    with get_session() as s:
        s.add(DecisionPoint(meeting_id=mid, text="keep me", settled=True,
                            positions=[], citations=[1], provenance="inferred"))

    def fake_extract(meeting_id):
        with get_session() as s:
            s.add(KeyTopic(meeting_id=meeting_id, title="regenerated",
                           citations=[1], provenance="inferred"))
    monkeypatch.setattr("meeting_mgr.api.edits.extract_key_topics", fake_extract)

    r = TestClient(app).post(f"/meetings/{mid}/regenerate/key_topics")
    assert r.status_code == 202
    with get_session() as s:
        topics = s.query(KeyTopic).filter_by(meeting_id=mid).all()
        assert [t.title for t in topics] == ["regenerated"]
        assert s.query(DecisionPoint).filter_by(meeting_id=mid).count() == 1, \
            "regenerating one type must not touch the others"

def test_regenerate_dispatches_via_celery_delay_not_inline(monkeypatch):
    """task_always_eager (set in conftest for this test session) makes
    `.delay()` execute synchronously, which hides whether the endpoint truly
    enqueues or just calls the extractor inline. This test does not rely on
    eager mode at all: it replaces `.delay` itself with a spy, so the
    assertion holds regardless of eager mode, and fails if the endpoint ever
    calls `_run_extraction` directly instead of going through Celery.
    """
    mid, _ = _meeting_with_topic()
    calls = []
    monkeypatch.setattr(edits._regenerate_task, "delay",
                        lambda *a, **kw: calls.append((a, kw)))

    r = TestClient(app).post(f"/meetings/{mid}/regenerate/key_topics")

    assert r.status_code == 202
    assert calls == [((mid, "key_topics"), {})], \
        "regenerate must dispatch through Celery's .delay, not run inline"
