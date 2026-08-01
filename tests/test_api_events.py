"""SSE progress endpoint tests.

These use a real, live uvicorn server + httpx over a socket, not
FastAPI's TestClient. In this repo's pinned starlette/httpx versions,
TestClient.stream() does not actually stream: it drives the whole ASGI
app to completion inside handle_request() before returning control to
the caller (see starlette.testclient.TransportForBackground.handle_request,
which awaits the app fully before the `with client.stream(...)` context
manager yields anything). Our endpoint blocks in subscribe() until a
terminal stage fires, so under TestClient the request-affirming call
itself hangs forever, before delivering even the first SSE line. A real
server has no such buffering, so it genuinely proves the endpoint
streams incrementally.
"""

import json
import threading
import time
import uuid

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization
from meeting_mgr.progress import publish


def _meeting(status="processing", stage="transcribe") -> int:
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(
            organization_id=org.id,
            title="t",
            status=status,
            current_stage=stage,
            visibility="organization",
        )
        s.add(m)
        s.flush()
        return m.id


def _login(client: httpx.Client) -> None:
    email = f"events-{uuid.uuid4()}@x.com"
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        acct = Account(organization_id=org.id, email=email, password_hash=hash_password("pw"))
        s.add(acct)
        s.flush()
    r = client.post("/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200


@pytest.fixture
def live_client():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10) as c:
        yield c
    server.should_exit = True
    thread.join(timeout=5)


def test_stream_opens_with_a_snapshot_of_current_state(live_client):
    _login(live_client)
    mid = _meeting()
    with live_client.stream("GET", f"/meetings/{mid}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data:"):
                snap = json.loads(line.removeprefix("data:").strip())
                assert snap["status"] == "processing"
                assert snap["current_stage"] == "transcribe"
                break


def test_stream_delivers_published_transitions(live_client):
    _login(live_client)
    mid = _meeting()
    seen = []

    def publish_soon():
        time.sleep(0.5)
        publish(mid, "align", "started")
        publish(mid, "align", "finished")

    threading.Thread(target=publish_soon, daemon=True).start()
    with live_client.stream("GET", f"/meetings/{mid}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                payload = json.loads(line.removeprefix("data:").strip())
                seen.append(payload)
                if payload.get("state") == "finished":
                    break
    assert {"stage": "align", "state": "started"} in seen


def test_stream_closes_on_a_live_failed_stage_not_only_on_reconnect(live_client):
    """A pipeline that dies mid-run must not hold the connection open forever.

    Unlike test_stream_delivers_published_transitions, this loop does NOT
    break on seeing the failed event — it lets iter_lines() run to natural
    exhaustion. If the server generator did not return on a live failed
    event, this test would hang until the client's 10s timeout and fail.
    """
    _login(live_client)
    mid = _meeting()

    def fail_soon():
        time.sleep(0.5)
        publish(mid, "transcribe", "failed")

    threading.Thread(target=fail_soon, daemon=True).start()
    seen = []
    with live_client.stream("GET", f"/meetings/{mid}/events") as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                seen.append(json.loads(line.removeprefix("data:").strip()))
    assert {"stage": "transcribe", "state": "failed"} in seen


def test_events_requires_authentication():
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="processing")
        s.add(m)
        s.flush()
        mid = m.id
    r = TestClient(app).get(f"/meetings/{mid}/events")
    assert r.status_code == 401
