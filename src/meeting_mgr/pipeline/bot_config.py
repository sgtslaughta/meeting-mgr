"""Bot-sweep constants shared between pipeline/bot.py (the task) and
pipeline/app.py (the beat schedule). Split into its own module, rather than
living in pipeline/bot.py, specifically so pipeline/app.py does not import
pipeline/bot.py -- pipeline/bot.py imports celery_app FROM pipeline/app.py,
so the reverse import would be a cycle. Same split pipeline/watch_config.py
established in Phase 5."""

STALE_SESSION_SECONDS = 14400  # 4 hours of no chunk upload marks a session stale
BOT_SWEEP_INTERVAL_SECONDS = 900  # Celery beat cadence for sweep_stale_bot_sessions
