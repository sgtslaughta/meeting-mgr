import logging
import os
import time
import uuid

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization, WatchFolder
from meeting_mgr.pipeline.watch import scan_watch_folder
from meeting_mgr.storage import ensure_bucket


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _watch_folder(org_id: int, root: str) -> WatchFolder:
    with get_session() as s:
        a = Account(organization_id=org_id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        wf = WatchFolder(organization_id=org_id, owner_account_id=a.id, root_path=root)
        s.add(wf)
        s.flush()
        s.refresh(wf)
        return wf


def _age(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def test_scan_ingests_only_stable_files_and_leaves_fresh_ones(tmp_path):
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    stable = tmp_path / "old.wav"
    stable.write_bytes(b"x")
    _age(stable, 60)
    fresh = tmp_path / "new.wav"
    fresh.write_bytes(b"x")

    scan_watch_folder.run(wf.id, org_id)

    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 1
    assert not stable.exists()
    assert fresh.exists()


def test_scan_never_reingests_already_ingested_files(tmp_path):
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    f = tmp_path / "old.wav"
    f.write_bytes(b"x")
    _age(f, 60)

    scan_watch_folder.run(wf.id, org_id)
    scan_watch_folder.run(wf.id, org_id)  # simulates a watcher restart

    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 1


def test_scan_updates_the_heartbeat_even_with_nothing_to_ingest(tmp_path):
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))

    scan_watch_folder.run(wf.id, org_id)

    with get_session() as s:
        row = s.get(WatchFolder, wf.id)
        assert row.last_scan_at is not None
        assert row.last_scan_error is None


def test_one_failed_file_does_not_block_the_others(tmp_path, monkeypatch):
    """The mock fails inside put_stream (what ingest_file actually calls),
    not by replacing ingest_file wholesale -- ingest_file (Task 5) already
    owns the move to .failed/ as part of its own pre-commit exception
    handling, so a mock that bypasses ingest_file entirely would bypass the
    very code responsible for that move, and the .failed/bad.wav assertion
    below could never pass. scan_watch_folder deliberately does not
    duplicate that move -- doing so risks fighting ingest_file's
    transaction-scoped move-then-commit ordering."""
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"x")
    _age(bad, 60)
    good = tmp_path / "good.wav"
    good.write_bytes(b"x")
    _age(good, 60)

    from meeting_mgr import pipeline

    real_put_stream = pipeline.watch.put_stream

    def _flaky_put_stream(key, fh):
        if "bad" in getattr(fh, "name", ""):
            raise RuntimeError("bad recording")
        return real_put_stream(key, fh)

    monkeypatch.setattr(pipeline.watch, "put_stream", _flaky_put_stream)
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)

    scan_watch_folder.run(wf.id, org_id)

    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 1
        row = s.get(WatchFolder, wf.id)
        assert row.last_scan_error is not None
        assert "bad.wav" in row.last_scan_error
    assert (tmp_path / ".failed" / "bad.wav").exists()
    assert (tmp_path / ".ingested" / "good.wav").exists()


def test_failed_file_is_logged_with_path_and_watch_folder_id(tmp_path, monkeypatch, caplog):
    """Per-file isolation without logging makes a file that fails every
    scan invisible to an operator forever -- Phase 4 established this
    pattern in purge_organization/sweep_retention. Assert the log line
    actually names this watch_folder_id and this file, not just that
    *something* was logged."""
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"x")
    _age(bad, 60)

    from meeting_mgr import pipeline

    def _always_fails(watch_folder, path):
        raise RuntimeError("corrupt recording")

    monkeypatch.setattr(pipeline.watch, "ingest_file", _always_fails)

    with caplog.at_level(logging.ERROR, logger="meeting_mgr.pipeline.watch"):
        scan_watch_folder.run(wf.id, org_id)

    assert any(str(bad) in r.message and str(wf.id) in r.message for r in caplog.records)


def test_scan_of_a_disabled_folder_is_a_no_op(tmp_path):
    ensure_bucket()
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    with get_session() as s:
        s.get(WatchFolder, wf.id).enabled = False
    f = tmp_path / "old.wav"
    f.write_bytes(b"x")
    _age(f, 60)

    scan_watch_folder.run(wf.id, org_id)

    assert f.exists()
    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 0
