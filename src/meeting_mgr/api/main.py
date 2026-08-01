from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from meeting_mgr.api.audit_log import router as audit_log_router
from meeting_mgr.api.auth import router as auth_router
from meeting_mgr.api.bot_credentials import router as bot_credentials_router
from meeting_mgr.api.capture import router as capture_router
from meeting_mgr.api.edits import router as edits_router
from meeting_mgr.api.meetings import router
from meeting_mgr.api.retention import router as retention_router
from meeting_mgr.api.watch_folders import router as watch_folders_router
from meeting_mgr.auth.mtls import MTLSHeaderStripMiddleware
from meeting_mgr.config import get_settings

app = FastAPI(title="Meeting-MGR")
# Starlette's add_middleware() inserts at the front of the middleware list,
# so the LAST middleware added ends up OUTERMOST and runs first on each
# request. Adding the strip middleware here (before SessionMiddleware, below)
# makes SessionMiddleware outermost — safe only because SessionMiddleware
# never reads the mTLS identity header. The invariant that matters is: the
# strip must run before anything that DOES read that header. Any future
# middleware that reads x-ssl-client-subject must be added BEFORE this call
# (so it ends up innermost relative to the strip, running after it).
app.add_middleware(MTLSHeaderStripMiddleware, allowlist=get_settings().mtls_proxy_allowlist)
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret)
app.include_router(router)
app.include_router(edits_router)
app.include_router(auth_router)
app.include_router(audit_log_router)
app.include_router(retention_router)
app.include_router(watch_folders_router)
app.include_router(capture_router)
app.include_router(bot_credentials_router)


@app.get("/health")
def health():
    return {"status": "ok"}
