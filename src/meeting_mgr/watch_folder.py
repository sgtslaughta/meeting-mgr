"""Watch folder configuration -- pure query helpers, no side effects.

The actual polling/ingest loop lives in meeting_mgr.pipeline.watch; this
module is only the config table's read/write surface, used by both the
admin API (api/watch_folders.py) and the scan dispatcher (pipeline/watch.py).
"""

from meeting_mgr.models import WatchFolder


def get_watch_folder(s, org_id: int, watch_folder_id: int) -> WatchFolder | None:
    return s.query(WatchFolder).filter_by(id=watch_folder_id, organization_id=org_id).one_or_none()


def list_watch_folders(s, org_id: int) -> list[WatchFolder]:
    return s.query(WatchFolder).filter_by(organization_id=org_id).order_by(WatchFolder.id).all()


def upsert_watch_folder(
    s, org_id: int, *, root_path: str, owner_account_id: int, enabled: bool = True
) -> WatchFolder:
    wf = s.query(WatchFolder).filter_by(organization_id=org_id, root_path=root_path).one_or_none()
    if wf is None:
        wf = WatchFolder(organization_id=org_id, root_path=root_path)
        s.add(wf)
    wf.owner_account_id = owner_account_id
    wf.enabled = enabled
    s.flush()
    return wf
