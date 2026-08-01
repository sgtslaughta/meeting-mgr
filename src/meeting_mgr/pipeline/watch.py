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

from meeting_mgr.pipeline.watch_config import (
    SCAN_INTERVAL_SECONDS,  # noqa: F401 -- re-exported for pipeline/app.py's beat comment and any caller importing it from here
)

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
