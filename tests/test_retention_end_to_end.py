import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, AuditLogEntry, Meeting, Organization, Recording
from meeting_mgr.pipeline.purge import purge_organization
from meeting_mgr.storage import ensure_bucket, get_object, put_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _admin(org_id) -> str:
    email = f"admin-{uuid.uuid4()}@x.com"
    with get_session() as s:
        s.add(
            Account(
                organization_id=org_id, email=email, role="admin", password_hash=hash_password("pw")
            )
        )
    return email


def _client_as(email: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/auth/login", json={"email": email, "password": "pw"}).status_code == 200
    return c


def _stale_meeting(org_id: int) -> tuple[int, str]:
    ensure_bucket()
    with get_session() as s:
        m = Meeting(
            organization_id=org_id,
            title="quarterly review",
            created_at=datetime.utcnow() - timedelta(days=400),
        )
        s.add(m)
        s.flush()
        key = f"raw/{m.id}"
        put_object(key, b"audio")
        s.add(Recording(meeting_id=m.id, raw_key=key))
        return m.id, key


def test_full_retention_lifecycle_via_the_http_api():
    org_id = _org()
    meeting_id, raw_key = _stale_meeting(org_id)
    admin_email = _admin(org_id)
    c = _client_as(admin_email)

    # 1. No policy: preview is empty, nothing eligible.
    assert c.get("/retention-policy/preview").json() == []

    # 2. Configure: purge whole meetings after 30 days.
    r = c.put("/retention-policy", json={"meeting_retention_days": 30})
    assert r.status_code == 200

    # 3. Dry run shows the stale meeting as a "full" candidate, unconfirmed.
    preview = c.get("/retention-policy/preview").json()
    assert len(preview) == 1
    assert preview[0]["meeting_id"] == meeting_id
    assert preview[0]["kind"] == "full"

    # 4. Dry run must not have deleted anything.
    with get_session() as s:
        assert s.get(Meeting, meeting_id) is not None

    # 5. Run the purge for real (celery_app.conf.task_always_eager makes
    # .delay() synchronous in tests -- see conftest.py).
    purge_organization(org_id)

    # 6. Row and object are both gone; an audit entry survives the meeting.
    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        entry = (
            s.query(AuditLogEntry)
            .filter_by(organization_id=org_id, action="meeting.purge.full")
            .one()
        )
        assert entry.target == f"meeting:{meeting_id}"

    import pytest
    from botocore.exceptions import ClientError

    with pytest.raises(ClientError):
        get_object(raw_key)

    # 7. Preview is empty again -- nothing left to purge.
    assert c.get("/retention-policy/preview").json() == []

    # 8. A second purge run is a safe no-op (nothing to select, per Task 4's
    # disjointness/idempotency).
    purge_organization(org_id)  # must not raise
