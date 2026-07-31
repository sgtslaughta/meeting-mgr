from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from meeting_mgr.api.auth import router as auth_router
from meeting_mgr.api.edits import router as edits_router
from meeting_mgr.api.meetings import router
from meeting_mgr.config import get_settings

app = FastAPI(title="Meeting-MGR")
app.add_middleware(SessionMiddleware, secret_key=get_settings().session_secret)
app.include_router(router)
app.include_router(edits_router)
app.include_router(auth_router)


@app.get("/health")
def health():
    return {"status": "ok"}
