import json
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from meeting_mgr.audit import record_audit
from meeting_mgr.auth.bot_deps import get_bot_credential
from meeting_mgr.chunk_storage import chunk_key, chunk_prefix, chunk_seq
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import BotCredential, BotSession, Meeting, Recording
from meeting_mgr.storage import ensure_bucket, list_keys, put_object, put_stream

router = APIRouter(prefix="/bot")
_SUBDIR, _SUFFIX = "bot-chunks", ".chunk"


class StartSessionIn(BaseModel):
    platform_meeting_id: str
    title: str


def _response(session: BotSession, meeting: Meeting, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"session_id": session.id, "meeting_id": meeting.id, "status": meeting.status},
    )


@router.post("/sessions", status_code=201)
def start_session(body: StartSessionIn, credential: BotCredential = Depends(get_bot_credential)):
    # No require_role()/authorize(): a bot is not an Account and no Meeting
    # exists yet to authorize against. The credential itself -- valid,
    # unrevoked, resolved by get_bot_credential -- is the authorization: it
    # may only ever act as its own organization_id, which is the sole source
    # of tenancy here (never client input).
    with get_org_session(credential.organization_id) as s:
        existing = (
            s.query(BotSession)
            .filter_by(
                bot_credential_id=credential.id, platform_meeting_id=body.platform_meeting_id
            )
            .one_or_none()
        )
        if existing is not None:
            m = s.get(Meeting, existing.meeting_id)
            return _response(existing, m, status_code=200)

        try:
            # SAVEPOINT: a concurrent request that also passed the
            # check-above can still win the uq_bot_session_credential_platform
            # race at INSERT time. Only this nested block rolls back on that
            # conflict, not the whole session -- and the loser replays the
            # winner's row as an ordinary 200 instead of surfacing the
            # IntegrityError as a 500. A bot retrying after a network blip
            # must see idempotent success, not an error to retry again.
            with s.begin_nested():
                m = Meeting(
                    organization_id=credential.organization_id,
                    owner_account_id=credential.owner_account_id,
                    title=body.title,
                    status="capturing",
                )
                s.add(m)
                s.flush()
                session = BotSession(
                    organization_id=credential.organization_id,
                    bot_credential_id=credential.id,
                    meeting_id=m.id,
                    platform_meeting_id=body.platform_meeting_id,
                )
                s.add(session)
                s.flush()
        except IntegrityError:
            session = (
                s.query(BotSession)
                .filter_by(
                    bot_credential_id=credential.id,
                    platform_meeting_id=body.platform_meeting_id,
                )
                .one()
            )
            m = s.get(Meeting, session.meeting_id)
            return _response(session, m, status_code=200)
        return _response(session, m, status_code=201)


def _bot_chunk_prefix(meeting_id: int) -> str:
    return chunk_prefix(meeting_id, _SUBDIR)


def _bot_chunk_key(meeting_id: int, seq: int) -> str:
    return chunk_key(meeting_id, seq, _SUBDIR, _SUFFIX)


def _bot_chunk_seq(prefix: str, key: str) -> int:
    return chunk_seq(prefix, _SUFFIX, key)


def _owned_session(s, session_id: int, credential: BotCredential) -> BotSession:
    """Look up a session and verify it belongs to the authenticated
    credential -- 404, not 403, on any mismatch (wrong credential or wrong
    organization), same reasoning as authz.authorize()'s tenant-mismatch
    branch: a distinguishable status code would let one bot credential
    enumerate another's session ids."""
    session = (
        s.query(BotSession)
        .filter_by(
            id=session_id,
            bot_credential_id=credential.id,
            organization_id=credential.organization_id,
        )
        .one_or_none()
    )
    if session is None:
        raise HTTPException(404, "bot session not found")
    return session


@router.put("/sessions/{session_id}/chunks/{seq}")
def upload_chunk(
    session_id: int,
    seq: int,
    chunk: UploadFile = File(...),
    credential: BotCredential = Depends(get_bot_credential),
):
    with get_org_session(credential.organization_id) as s:
        session = _owned_session(s, session_id, credential)
        m = s.get(Meeting, session.meeting_id)
        if m.status != "capturing":
            raise HTTPException(409, "session is not accepting chunks")
        meeting_id = m.id
        ensure_bucket()
        put_stream(_bot_chunk_key(meeting_id, seq), chunk.file)
        session.last_activity_at = datetime.utcnow()
    return {"seq": seq}


@router.get("/sessions/{session_id}/chunks")
def list_chunks(session_id: int, credential: BotCredential = Depends(get_bot_credential)):
    with get_readonly_org_session(credential.organization_id) as s:
        session = _owned_session(s, session_id, credential)
        meeting_id = session.meeting_id
    prefix = _bot_chunk_prefix(meeting_id)
    seqs = sorted(_bot_chunk_seq(prefix, k) for k in list_keys(prefix))
    return {"seqs": seqs}


def run_pipeline(meeting_id: int) -> None:
    """Module-level indirection, same pattern as api/capture.py and
    pipeline/watch.py -- deferred import, monkeypatchable in tests. Defined
    here rather than imported from api/capture.py so this module has no
    import-time dependency on an unrelated adapter's file."""
    from meeting_mgr.pipeline.orchestrate import run_pipeline as task

    task.delay(meeting_id)


def _finish_race_hook(meeting_id: int) -> None:
    """No-op in production. Runs after finish_session's status flip commits
    but before it takes the list_keys() snapshot -- tests monkeypatch this
    to simulate a straggler chunk upload landing in that window. Mirrors
    api/capture.py::_finish_race_hook exactly; see its finish_capture for
    the full TOCTOU ordering rationale."""


@router.post("/sessions/{session_id}/finish")
def finish_session(session_id: int, credential: BotCredential = Depends(get_bot_credential)):
    with get_org_session(credential.organization_id) as s:
        session = _owned_session(s, session_id, credential)
        m = s.get(Meeting, session.meeting_id)
        if m.status != "capturing":
            # Covers both a second finish() call and any other state the
            # Meeting has already left "capturing" for. Deliberately NOT
            # retryable: a second finish() call while status == "finishing"
            # still 409s here rather than resuming, because the first call
            # may still be genuinely in flight (mid-flight status flip is
            # commited well before its manifest work completes -- see
            # below) -- resuming from a second concurrent request risks a
            # duplicate Recording row. sweep_stale_bot_sessions
            # (pipeline/bot.py) is therefore the ONLY recovery path for a
            # Meeting stranded in "finishing" by a crash: it now sweeps
            # "finishing" (not just "capturing") past the same staleness
            # threshold and fails it out with a distinct failed_stage. A
            # Meeting genuinely mid-finish (not crashed) is never touched by
            # it: the sweep's own re-check right before writing, and the
            # staleness cutoff, both exist precisely so a normal finish() in
            # progress is not raced.
            raise HTTPException(409, "session is not in a capturing state")
        meeting_id = m.id
        # TOCTOU fix: flip status FIRST, committed in this transaction
        # alone, before taking the list_keys() snapshot below -- see
        # api/capture.py::finish_capture for the full ordering-guarantee /
        # residual-window rationale, which applies here unchanged. In short:
        # this closes the window for any upload_chunk whose OWN status
        # recheck begins after this commit (it now sees "finishing" and is
        # rejected with 409 before writing to S3), but does NOT close the
        # window for a straggler whose recheck already committed
        # "capturing" moments earlier and is still completing its
        # put_stream() when the snapshot below runs.
        m.status = "finishing"
    _finish_race_hook(meeting_id)
    prefix = _bot_chunk_prefix(meeting_id)
    # Ordered on the parsed integer sequence, never lexically -- "10"
    # sorts before "9" as a string, which would silently scramble chunk
    # order past ten chunks. Same reasoning as capture.py's finish.
    keys = sorted(list_keys(prefix), key=lambda k: _bot_chunk_seq(prefix, k))

    with get_org_session(credential.organization_id) as s:
        m = s.get(Meeting, meeting_id)
        if not keys:
            # No pipeline run to enqueue -- there is nothing to process --
            # and this is not a 4xx: the bot process is not a human who
            # would see an error code and retry. The failure must be
            # visible later, through the ordinary Meeting list, to whoever
            # owns it. failed_stage="bot_ingest" is not one of the real
            # pipeline stage names (orchestrate.py's STAGES), so it reads
            # distinctly from "a pipeline stage failed."
            m.status, m.failed_stage = "failed", "bot_ingest"
            record_audit(
                s,
                organization_id=credential.organization_id,
                actor_account_id=None,
                action="meeting.bot_ingest.empty",
                target=f"meeting:{meeting_id}",
            )
            return {"meeting_id": meeting_id, "status": "failed"}

        manifest_key = f"raw/{meeting_id}/bot-manifest.json"
        put_object(manifest_key, json.dumps(keys).encode())
        s.add(Recording(meeting_id=meeting_id, raw_key=f"manifest:{manifest_key}"))
        m.status = "pending"
        record_audit(
            s,
            organization_id=credential.organization_id,
            actor_account_id=None,
            action="meeting.bot_ingest.finish",
            target=f"meeting:{meeting_id}",
            detail={"chunk_count": len(keys)},
        )
    run_pipeline(meeting_id)
    return {"meeting_id": meeting_id, "status": "pending"}
