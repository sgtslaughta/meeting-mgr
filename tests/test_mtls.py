from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from meeting_mgr.auth.mtls import MTLS_SUBJECT_HEADER, MTLSHeaderStripMiddleware


def _app(allowlist: frozenset[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(MTLSHeaderStripMiddleware, allowlist=allowlist)

    @app.get("/whoami")
    def whoami(request: Request):
        return {"subject": request.headers.get(MTLS_SUBJECT_HEADER)}

    return app


def test_header_from_an_untrusted_source_is_stripped():
    # TestClient's requests present client host "testclient" — not allowlisted.
    c = TestClient(_app(allowlist=frozenset()))
    r = c.get("/whoami", headers={MTLS_SUBJECT_HEADER: "cn=admin@evil.example"})
    assert r.json()["subject"] is None, (
        "a forged identity header from a non-allowlisted source must be invisible to the app"
    )


def test_header_from_an_allowlisted_source_passes_through():
    c = TestClient(_app(allowlist=frozenset({"testclient"})))
    r = c.get("/whoami", headers={MTLS_SUBJECT_HEADER: "cn=alice@example.com"})
    assert r.json()["subject"] == "cn=alice@example.com"


def test_empty_allowlist_strips_from_every_source():
    # The default posture: mTLS off means the header is never trusted, from anywhere.
    c = TestClient(_app(allowlist=frozenset()))
    r = c.get("/whoami", headers={MTLS_SUBJECT_HEADER: "cn=anyone"})
    assert r.json()["subject"] is None


def test_requests_without_the_header_are_unaffected():
    c = TestClient(_app(allowlist=frozenset({"testclient"})))
    r = c.get("/whoami")
    assert r.json()["subject"] is None


def test_header_stripped_regardless_of_case():
    # HTTP headers are case-insensitive; a case-sensitive strip is a bypass.
    c = TestClient(_app(allowlist=frozenset()))
    r = c.get("/whoami", headers={"X-SSL-Client-Subject": "cn=admin@evil.example"})
    assert r.json()["subject"] is None


def test_middleware_strips_mixed_case_header_bytes_directly():
    # ASGI servers normalize header names to lowercase before the app ever
    # sees them, so a TestClient round-trip can't prove the middleware's own
    # comparison is case-insensitive — it would pass even if the middleware
    # compared case-sensitively. Drive the ASGI interface directly with
    # mixed-case header bytes to exercise the middleware's own logic.
    import asyncio

    from meeting_mgr.auth.mtls import MTLSHeaderStripMiddleware

    async def scenario():
        captured = {}

        async def inner_app(scope, receive, send):
            captured["headers"] = scope["headers"]

        middleware = MTLSHeaderStripMiddleware(inner_app, allowlist=frozenset())
        scope = {
            "type": "http",
            "client": ("203.0.113.5", 12345),
            "headers": [(b"X-SSL-Client-Subject", b"cn=admin@evil.example")],
        }

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            pass

        await middleware(scope, receive, send)
        return captured["headers"]

    headers = asyncio.run(scenario())
    assert headers == [], (
        "mixed-case header bytes must still be stripped by the middleware's own comparison"
    )
