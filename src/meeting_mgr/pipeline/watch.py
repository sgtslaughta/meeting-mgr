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
from datetime import datetime

from meeting_mgr.db import get_org_session
from meeting_mgr.models import Meeting, Recording, WatchFolder
from meeting_mgr.pipeline.app import celery_app
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


def _candidate_files(root: str):
    """Walk root for ingest candidates, never descending into a directory
    whose name starts with "." -- .ingested/ and .failed/ live inside the
    watched root (see ingest_file()), and that placement is the whole
    idempotency mechanism: re-descending into them would hand an
    already-moved file straight back to the scanner as if it were new."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            yield os.path.join(dirpath, name)


@celery_app.task(name="meeting_mgr.scan_watch_folder")
def scan_watch_folder(watch_folder_id: int, organization_id: int) -> None:
    """Scan one WatchFolder: ingest every stable candidate, isolating
    per-file failures, then update the last_scan_at/last_scan_error
    heartbeat GET /watch-folders (Task 3) uses to flag a stalled watcher.

    Identity (organization_id, owner_account_id) is read off the WatchFolder
    row fetched via get_org_session(organization_id) -- never guessed from
    the filesystem path or a default -- and the id/org pair always travels
    together as this task's own arguments, dispatched by
    scan_watch_folders() (Task 7).

    One file failing to ingest (unreadable, corrupt, a storage outage) must
    not abort the rest of the folder -- same isolation-plus-logging
    discipline Phase 4 established in purge_organization/sweep_retention:
    swallowing the exception without logging it would make a file that
    fails every single scan invisible to an operator forever. ingest_file()
    already logs path/meeting_id/error and moves the source to .failed/
    before re-raising; this loop additionally logs watch_folder_id so an
    operator can tell which folder is unhealthy, then folds every message
    into last_scan_error for the heartbeat.

    The heartbeat is updated unconditionally after the loop -- including
    when every file failed -- specifically so a folder full of bad files
    reads as "scanned, with errors" rather than "stalled": last_scan_at
    is the only signal an operator has that the watcher process itself is
    still alive and taking scans."""
    with get_org_session(organization_id) as s:
        wf = (
            s.query(WatchFolder)
            .filter_by(id=watch_folder_id, organization_id=organization_id)
            .one_or_none()
        )
        root_path, enabled = (wf.root_path, wf.enabled) if wf else (None, False)

    if not enabled:
        return

    errors: list[str] = []
    for path in _candidate_files(root_path):
        if not _is_stable(path):
            continue
        try:
            ingest_file(wf, path)
        except Exception as exc:
            logger.exception(
                "scan_watch_folder: failed to ingest path=%s watch_folder_id=%s error=%r",
                path,
                watch_folder_id,
                exc,
            )
            errors.append(f"{os.path.relpath(path, root_path)}: {exc}")

    with get_org_session(organization_id) as s:
        row = s.get(WatchFolder, watch_folder_id)
        row.last_scan_at = datetime.utcnow()
        row.last_scan_error = "; ".join(errors) if errors else None
