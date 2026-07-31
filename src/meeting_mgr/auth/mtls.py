"""The mTLS trust boundary.

When mTLS terminates at a reverse proxy, the app learns the client's identity
from a forwarded header. Without a control here, any client able to reach the
API directly could authenticate as anyone by setting that header itself. This
middleware strips the header unless the request's own source IP — not a
header, something the network layer itself reports — is on an explicit
allowlist. Everything downstream of this middleware may treat the header's
mere presence as proof of a trusted proxy.
"""

MTLS_SUBJECT_HEADER = "x-ssl-client-subject"
_HEADER_BYTES = MTLS_SUBJECT_HEADER.encode()


class MTLSHeaderStripMiddleware:
    def __init__(self, app, allowlist: frozenset[str]):
        self.app = app
        self.allowlist = allowlist

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            client = scope.get("client")
            source_ip = client[0] if client else None
            if source_ip not in self.allowlist:
                scope = dict(scope)
                # Header names in an ASGI scope are lowercased per spec, but
                # compare case-insensitively anyway — the security property
                # must not depend on that normalization holding everywhere.
                scope["headers"] = [
                    (k, v) for k, v in scope["headers"] if k.lower() != _HEADER_BYTES
                ]
        await self.app(scope, receive, send)
