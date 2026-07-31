"""Stage-transition pub/sub.

The Celery worker and the API run in different containers, so progress travels
through Redis — already present as the broker, so no new service.
"""

import json

import redis

from meeting_mgr.config import get_settings


def _redis():
    return redis.Redis.from_url(get_settings().redis_url)


def channel(meeting_id: int) -> str:
    return f"meeting:{meeting_id}:progress"


def publish(meeting_id: int, stage: str, state: str) -> None:
    """Announce a stage transition. Never raises.

    Progress is a convenience; a dead Redis must not fail a pipeline run that
    may represent an hour of GPU work.
    """
    try:
        _redis().publish(channel(meeting_id), json.dumps({"stage": stage, "state": state}))
    except Exception:
        pass


def subscribe(meeting_id: int):
    """Yield transition dicts for one meeting until the caller stops."""
    pubsub = _redis().pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(channel(meeting_id))
    try:
        for message in pubsub.listen():
            if message.get("type") == "message":
                yield json.loads(message["data"])
    finally:
        pubsub.close()
