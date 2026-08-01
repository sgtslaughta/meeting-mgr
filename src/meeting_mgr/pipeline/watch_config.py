"""Watch-folder constants shared across api/watch_folders.py (Task 3) and
pipeline/watch.py (Task 4). Split into its own module, rather than living
in pipeline/watch.py, specifically so this task does not have a forward
dependency on Task 4 -- pipeline/watch.py imports SCAN_INTERVAL_SECONDS
from here instead of defining it."""

SCAN_INTERVAL_SECONDS = 300  # Celery beat cadence for scan_watch_folders (Task 7)
