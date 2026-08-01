import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Account, Organization, WatchFolder


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id: int) -> int:
    with get_session() as s:
        a = Account(organization_id=org_id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return a.id


def test_watch_folder_defaults_enabled_with_no_heartbeat_yet():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        wf = WatchFolder(organization_id=org_id, owner_account_id=account_id, root_path="/data/a")
        s.add(wf)
        s.flush()
        assert wf.enabled is True
        assert wf.last_scan_at is None
        assert wf.last_scan_error is None


def test_watch_folder_is_unique_per_organization_and_path():
    org_id = _org()
    account_id = _account(org_id)
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(
                WatchFolder(
                    organization_id=org_id, owner_account_id=account_id, root_path="/data/a"
                )
            )
            s.add(
                WatchFolder(
                    organization_id=org_id, owner_account_id=account_id, root_path="/data/a"
                )
            )
            s.flush()


def test_watch_folder_tenant_isolation():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_session() as s:
        s.add(WatchFolder(organization_id=org_a, owner_account_id=account_a, root_path="/data/a"))
        s.add(WatchFolder(organization_id=org_b, owner_account_id=account_b, root_path="/data/b"))

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT root_path FROM watch_folder")).fetchall()
        paths = {r[0] for r in rows}
    assert "/data/a" in paths
    assert "/data/b" not in paths, "RLS did not confine watch_folder to its own organization"
