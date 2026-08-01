import uuid

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization
from meeting_mgr.watch_folder import get_watch_folder, list_watch_folders, upsert_watch_folder


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


def test_get_watch_folder_returns_none_when_absent():
    org_id = _org()
    with get_session() as s:
        assert get_watch_folder(s, org_id, 999999) is None


def test_upsert_creates_then_updates_the_same_row_by_path():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        wf1 = upsert_watch_folder(s, org_id, root_path="/data/a", owner_account_id=account_id)
        row_id = wf1.id

    with get_session() as s:
        wf2 = upsert_watch_folder(
            s, org_id, root_path="/data/a", owner_account_id=account_id, enabled=False
        )
        assert wf2.id == row_id, "a second call for the same path must update, not duplicate"
        assert wf2.enabled is False


def test_list_watch_folders_scopes_by_organization():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_session() as s:
        upsert_watch_folder(s, org_a, root_path="/data/a", owner_account_id=account_a)
        upsert_watch_folder(s, org_b, root_path="/data/b", owner_account_id=account_b)

    with get_session() as s:
        paths = {wf.root_path for wf in list_watch_folders(s, org_a)}
    assert paths == {"/data/a"}
