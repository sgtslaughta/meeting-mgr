import json
import threading
import time
from meeting_mgr.progress import channel, publish, subscribe

def test_channel_is_scoped_per_meeting():
    assert channel(7) == "meeting:7:progress"
    assert channel(8) != channel(7)

def test_published_events_reach_a_subscriber():
    received = []
    ready = threading.Event()

    def listen():
        for event in subscribe(4242):
            ready.set()
            received.append(event)
            if event["state"] == "finished":
                break

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    # subscribe() must be listening before publish, or the message is lost —
    # Redis pub/sub has no backlog.
    time.sleep(0.3)
    publish(4242, "transcribe", "started")
    publish(4242, "transcribe", "finished")
    t.join(timeout=5)

    assert {"stage": "transcribe", "state": "started"} in received
    assert {"stage": "transcribe", "state": "finished"} in received

def test_publish_never_raises_when_redis_is_unreachable(monkeypatch):
    import meeting_mgr.progress as mod
    def boom():
        raise ConnectionError("redis is down")
    monkeypatch.setattr(mod, "_redis", boom)
    publish(1, "normalize", "started")   # must not raise
