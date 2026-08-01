import io
import uuid

import pytest

from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, Organization, Recording, WatchFolder
from meeting_mgr.storage import ensure_bucket, get_object


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


def test_ingest_file_creates_meeting_and_recording_under_the_folders_org_and_owner(
    tmp_path, monkeypatch
):
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    meeting_id = pipeline.watch.ingest_file(wf, str(src))

    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.organization_id == org_id
        assert m.owner_account_id == wf.owner_account_id
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        assert get_object(rec.raw_key) == b"audio-bytes"


def test_ingest_file_moves_the_source_into_dot_ingested(tmp_path, monkeypatch):
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    pipeline.watch.ingest_file(wf, str(src))

    assert not src.exists()
    assert (tmp_path / ".ingested" / "rec.wav").exists()


def test_ingest_file_does_not_reingest_on_a_rescan_of_the_root(tmp_path, monkeypatch):
    """File disposition IS the idempotency key: a restarted scan re-lists the
    root and, once the file has moved to .ingested/, finds nothing there to
    ingest again."""
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    first_id = pipeline.watch.ingest_file(wf, str(src))

    # Simulate a re-scan of the root: nothing left at the original path to
    # find and re-ingest.
    import os

    assert set(os.listdir(tmp_path)) == {".ingested"}

    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 1
    assert first_id > 0


def test_ingest_file_moves_the_source_into_dot_failed_and_reraises_on_upload_error(
    tmp_path, monkeypatch
):
    from meeting_mgr import pipeline

    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    def _boom(key, fileobj):
        raise RuntimeError("storage is down")

    monkeypatch.setattr(pipeline.watch, "put_stream", _boom)

    with pytest.raises(RuntimeError):
        pipeline.watch.ingest_file(wf, str(src))

    assert not src.exists()
    assert (tmp_path / ".failed" / "rec.wav").exists()
    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 0


def test_ingest_file_a_nested_relative_path_is_preserved_under_dot_ingested(tmp_path, monkeypatch):
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    nested = tmp_path / "2026" / "07"
    nested.mkdir(parents=True)
    src = nested / "rec.wav"
    src.write_bytes(b"audio-bytes")

    pipeline.watch.ingest_file(wf, str(src))

    assert (tmp_path / ".ingested" / "2026" / "07" / "rec.wav").exists()


def test_ingest_file_skips_a_file_that_changed_since_the_stability_check(tmp_path, monkeypatch):
    """A (size, mtime) snapshot is taken at the top of ingest_file and
    compared against a fresh snapshot immediately before the read. If a
    writer resumed in between -- simulated here with a deterministic
    sequence of return values rather than a real timing race -- the file
    must be skipped: no Meeting/Recording row, and the file left exactly
    where it is (not moved to .ingested/ or .failed/) for the next scan to
    re-judge once it's quiet again."""
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    signatures = iter([(11, 1000.0), (999, 2000.0)])
    monkeypatch.setattr(pipeline.watch, "_stat_signature", lambda path: next(signatures))
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    result = pipeline.watch.ingest_file(wf, str(src))

    assert result == 0
    assert src.exists()
    assert src.read_bytes() == b"audio-bytes"
    assert not (tmp_path / ".ingested").exists()
    assert not (tmp_path / ".failed").exists()
    with get_session() as s:
        assert s.query(Meeting).filter_by(organization_id=org_id).count() == 0


def test_ingest_file_streams_the_upload_rather_than_buffering_it_whole(tmp_path, monkeypatch):
    """put_stream must receive an open file handle it can read in chunks,
    never the file's bytes read fully into memory first."""
    from meeting_mgr import pipeline

    ensure_bucket()
    monkeypatch.setattr(pipeline.watch, "run_pipeline", lambda meeting_id: None)
    captured = {}

    def _spy(key, fileobj):
        captured["fileobj"] = fileobj
        captured["is_bytes"] = isinstance(fileobj, (bytes, bytearray))
        captured["has_chunked_read"] = hasattr(fileobj, "read") and hasattr(fileobj, "readable")

    monkeypatch.setattr(pipeline.watch, "put_stream", _spy)
    org_id = _org()
    wf = _watch_folder(org_id, str(tmp_path))
    src = tmp_path / "rec.wav"
    src.write_bytes(b"audio-bytes")

    pipeline.watch.ingest_file(wf, str(src))

    assert captured["is_bytes"] is False
    assert captured["has_chunked_read"] is True
    assert isinstance(captured["fileobj"], io.BufferedIOBase)
