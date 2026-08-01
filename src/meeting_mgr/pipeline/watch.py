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

import logging
import os
import time

from meeting_mgr.db import get_org_session
from meeting_mgr.models import Meeting, Recording
from meeting_mgr.pipeline.watch_config import (
    SCAN_INTERVAL_SECONDS,  # noqa: F401 -- re-exported for pipeline/app.py's beat comment and any caller importing it from here
)
from meeting_mgr.storage import ensure_bucket, put_stream

logger = logging.getLogger(__name__)

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


def ingest_file(watch_folder, path: str) -> int | None:
    """Ingest one file the caller has already judged stable via _is_stable().

    That check is point-in-time: nothing stops a copy that resumed after it
    from being read here truncated. The guard is a (size, mtime) snapshot
    taken as the first thing this function does -- as close as possible to
    the caller's own check -- compared against a second snapshot taken
    immediately before the open()/put_stream() read. A Meeting insert/flush
    (a network round trip to Postgres to get m.id for the storage key, not
    filesystem I/O -- it still widens the window) plus a handful of Python
    statements separate the two, so the unguarded gap is that round trip,
    not the callers'-check-to-read gap, which is fully covered. A mismatch
    means a writer resumed; the file is skipped this pass -- returns None,
    not ingested -- and picked up again once genuinely quiet.

    The move to .ingested/ happens INSIDE the transaction, before commit,
    not after: filesystem placement is the only idempotency key (no
    ingestion ledger), so a move that fails after the row was already
    committed would leave a committed Meeting whose source file is still
    sitting at its original path -- the next scan finds it and ingests it
    again, a second Meeting for the same bytes. Attempting the move before
    commit means a failed move raises inside the `with` block, which rolls
    the transaction back via the same path as any other ingest failure
    below: no row survives, and the file (still at its original path,
    os.replace() never partially applies) is handed to the existing
    .failed/ handling. The residual risk this doesn't remove is a crash
    between a successful move and the commit a few lines later, which
    would leave an orphaned S3 object and a relocated file with no Meeting
    row -- recoverable by an operator (the bytes are not gone), unlike the
    guaranteed duplicate the old commit-then-move order produced on every
    ordinary move failure (disk full, permissions, cross-device).

    Storage upload happens before the row is committed; if anything past
    this point raises, the Meeting/Recording insert rolls back (no row
    exists), the failure is logged with the path/meeting_id/error, the
    source is moved to .failed/ instead of .ingested/, and the exception is
    re-raised so scan_watch_folder() (Task 6) can record it as this scan's
    last_scan_error without aborting the scan for other files."""
    ensure_bucket()
    try:
        baseline = _stat_signature(path)
    except FileNotFoundError:
        return None

    meeting_id = None
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
            meeting_id = m.id
            key = f"raw/{m.id}/{os.path.basename(path)}"
            if _stat_signature(path) != baseline:
                raise _StaleFile(path)
            with open(path, "rb") as fh:
                put_stream(key, fh)
            s.add(Recording(meeting_id=m.id, raw_key=key))
            _move_into(watch_folder.root_path, ".ingested", path)
    except _StaleFile:
        return None
    except Exception as exc:
        logger.error(
            "watch folder ingest failed: path=%s meeting_id=%s error=%r",
            path,
            meeting_id,
            exc,
        )
        _move_into(watch_folder.root_path, ".failed", path)
        raise

    run_pipeline(meeting_id)
    return meeting_id
