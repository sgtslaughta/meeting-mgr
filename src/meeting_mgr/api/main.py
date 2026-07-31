from fastapi import FastAPI
from meeting_mgr.api.meetings import router

app = FastAPI(title="Meeting-MGR")
app.include_router(router)

@app.get("/health")
def health():
    return {"status": "ok"}
