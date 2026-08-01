"""Watch-folder ingest: polling, stability, and per-file ingest.

A file becomes a candidate only once _is_stable() judges it quiet -- a
half-copied Recording still being written has a recent st_mtime and is
skipped this scan, picked up on a later one once writes stop. This check is
stateless (no "last seen size" bookkeeping) so a watcher restart loses
nothing: the same wall-clock comparison gives the same answer next run.

Idempotency is filesystem placement, not a database ledger -- see
ingest_file() (Task 5) and scan_watch_folder() (Task 6).

SCAN_INTERVAL_SECONDS lives in pipeline/watch_config.py (Task 3), not here
-- api/watch_folders.py needs it too, and importing it back out of this
file would give Task 3 a forward dependency on this one.
"""

import os
import time

from meeting_mgr.db import get_org_session
from meeting_mgr.models import Meeting, Recording
from meeting_mgr.pipeline.watch_config import (
    SCAN_INTERVAL_SECONDS,  # noqa: F401 -- re-exported for pipeline/app.py's beat comment and any caller importing it from here
)
from meeting_mgr.storage import ensure_bucket, put_stream

STABLE_QUIET_SECONDS = 30


def _is_stable(
    path: str, *, quiet_seconds: int = STABLE_QUIET_SECONDS, now: float | None = None
) -> bool:
    now = now if now is not None else time.time()
    try:
        mtime = os.stat(path).st_mtime
    except FileNotFoundError:
        return False
    return (now - mtime) >= quiet_seconds


def run_pipeline(meeting_id: int) -> None:
    """Module-level indirection, same reason as api/meetings.py's copy:
    deferred import for load order, monkeypatchable in tests."""
    from meeting_mgr.pipeline.orchestrate import run_pipeline as task

    task.delay(meeting_id)


def _relpath(root: str, path: str) -> str:
    return os.path.relpath(path, root)


def _move_into(root: str, subdir: str, path: str) -> None:
    rel = _relpath(root, path)
    dest = os.path.join(root, subdir, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(path, dest)


def _stat_signature(path: str) -> tuple[int, float]:
    """(size, mtime) -- cheap enough to call twice per file, and unlike
    _is_stable()'s quiet-window predicate, an exact match is what actually
    proves nothing was written in between, regardless of how long that
    gap was."""
    st = os.stat(path)
    return (st.st_size, st.st_mtime)


class _StaleFile(Exception):
    """Internal signal only, never escapes ingest_file(): the file changed
    since the caller's _is_stable() check, caught by the re-verification
    immediately before the read. Not a failure -- the file is left in place
    (not moved to .failed/ or .ingested/) so the next scan re-judges it once
    it's quiet again. Deliberately not caught by the generic `except
    Exception` below, which is reserved for genuine upload/DB failures that
    DO move the file to .failed/ and re-raise."""


def ingest_file(watch_folder, path: str) -> int:
    """Ingest one file the caller has already judged stable via _is_stable().

    That check is point-in-time: nothing stops a copy that resumed after it
    from being read here truncated. The guard is a (size, mtime) snapshot
    taken as the first thing this function does -- as close as possible to
    the caller's own check -- compared against a second snapshot taken
    immediately before the open()/put_stream() read. Only a Meeting
    insert/flush (no filesystem I/O) separates the two, so the unguarded
    window is just that DB round trip plus the handful of Python statements
    on either side of it -- not the callers'-check-to-read gap, which is
    fully covered. A mismatch means a writer resumed; the file is skipped
    this pass, not ingested, and picked up again once genuinely quiet.

    Storage upload happens before the row is committed; if it raises, the
    Meeting/Recording insert rolls back (no row exists) and the source is
    moved to .failed/ instead of .ingested/, then the exception is
    re-raised so scan_watch_folder() (Task 6) can record it as this scan's
    last_scan_error without aborting the scan for other files."""
    ensure_bucket()
    try:
        baseline = _stat_signature(path)
    except FileNotFoundError:
        return 0

    try:
        with get_org_session(watch_folder.organization_id) as s:
            m = Meeting(
                organization_id=watch_folder.organization_id,
                owner_account_id=watch_folder.owner_account_id,
                title=os.path.basename(path),
                status="pending",
            )
            s.add(m)
            s.flush()
            key = f"raw/{m.id}/{os.path.basename(path)}"
            if _stat_signature(path) != baseline:
                raise _StaleFile(path)
            with open(path, "rb") as fh:
                put_stream(key, fh)
            s.add(Recording(meeting_id=m.id, raw_key=key))
            meeting_id = m.id
    except _StaleFile:
        return 0
    except Exception:
        _move_into(watch_folder.root_path, ".failed", path)
        raise

    _move_into(watch_folder.root_path, ".ingested", path)
    run_pipeline(meeting_id)
    return meeting_id
