import socket

import httpx


def test_chat_returns_queued_response(fake_inference):
    fake_inference.push_chat({"topics": ["budget"]})
    r = httpx.post(
        f"{fake_inference.base_url}/v1/chat/completions", json={"model": "m", "messages": []}
    )
    assert r.status_code == 200
    assert "budget" in r.json()["choices"][0]["message"]["content"]


def test_stop_releases_the_listening_socket(fake_inference):
    port = int(fake_inference.base_url.rsplit(":", 1)[1])
    fake_inference.stop()
    # Rebinding the same port must succeed once the socket is released.
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_error_response_has_a_json_body(fake_inference):
    fake_inference.push_error(503)
    r = httpx.post(
        f"{fake_inference.base_url}/v1/chat/completions", json={"model": "m", "messages": []}
    )
    assert r.status_code == 503
    assert r.json()["error"]["message"] == "fake failure"
    assert fake_inference.requests, "request should be recorded even when it errors"
