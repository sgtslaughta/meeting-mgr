import os
import time

from meeting_mgr.pipeline.watch import _is_stable


def test_a_freshly_written_file_is_not_stable(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"partial")
    assert _is_stable(str(p), quiet_seconds=30) is False


def test_a_quiet_file_is_stable(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"complete")
    old = time.time() - 60
    os.utime(p, (old, old))
    assert _is_stable(str(p), quiet_seconds=30) is True


def test_a_missing_file_is_never_stable(tmp_path):
    assert _is_stable(str(tmp_path / "nope.wav"), quiet_seconds=30) is False


def test_quiet_seconds_boundary_uses_the_injected_now(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"x")
    mtime = 1_000_000.0
    os.utime(p, (mtime, mtime))
    assert _is_stable(str(p), quiet_seconds=30, now=mtime + 29) is False
    assert _is_stable(str(p), quiet_seconds=30, now=mtime + 30) is True
