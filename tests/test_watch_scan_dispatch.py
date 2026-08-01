import uuid

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization, WatchFolder
from meeting_mgr.pipeline.watch import scan_watch_folders


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _watch_folder(org_id: int, root: str, enabled: bool = True) -> int:
    with get_session() as s:
        a = Account(organization_id=org_id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        wf = WatchFolder(
            organization_id=org_id, owner_account_id=a.id, root_path=root, enabled=enabled
        )
        s.add(wf)
        s.flush()
        return wf.id


def test_dispatch_only_enabled_folders(monkeypatch, tmp_path):
    from meeting_mgr import pipeline

    org_id = _org()
    enabled_id = _watch_folder(org_id, str(tmp_path / "a"))
    disabled_id = _watch_folder(org_id, str(tmp_path / "b"), enabled=False)

    dispatched = []
    monkeypatch.setattr(
        pipeline.watch.scan_watch_folder,
        "delay",
        lambda watch_folder_id, organization_id: dispatched.append(
            (watch_folder_id, organization_id)
        ),
    )

    scan_watch_folders.run()

    assert (enabled_id, org_id) in dispatched
    assert all(d[0] != disabled_id for d in dispatched)


def test_dispatch_across_organizations(monkeypatch, tmp_path):
    from meeting_mgr import pipeline

    org_a, org_b = _org(), _org()
    id_a = _watch_folder(org_a, str(tmp_path / "a"))
    id_b = _watch_folder(org_b, str(tmp_path / "b"))

    dispatched = []
    monkeypatch.setattr(
        pipeline.watch.scan_watch_folder,
        "delay",
        lambda watch_folder_id, organization_id: dispatched.append(
            (watch_folder_id, organization_id)
        ),
    )

    scan_watch_folders.run()

    assert (id_a, org_a) in dispatched
    assert (id_b, org_b) in dispatched


def test_disabled_folder_dispatch_failure_does_not_block_others(monkeypatch, tmp_path):
    """One folder's .delay() raising must not stop the sweep from
    dispatching the rest -- same isolation contract as sweep_retention."""
    from meeting_mgr import pipeline

    org_id = _org()
    failing_id = _watch_folder(org_id, str(tmp_path / "a"))
    ok_id = _watch_folder(org_id, str(tmp_path / "b"))

    dispatched = []

    def fake_delay(watch_folder_id, organization_id):
        if watch_folder_id == failing_id:
            raise RuntimeError("boom")
        dispatched.append((watch_folder_id, organization_id))

    monkeypatch.setattr(pipeline.watch.scan_watch_folder, "delay", fake_delay)

    scan_watch_folders.run()

    assert (ok_id, org_id) in dispatched
    assert all(d[0] != failing_id for d in dispatched)
