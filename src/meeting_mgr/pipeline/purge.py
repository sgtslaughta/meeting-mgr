"""Retention purge: the only code in the codebase that hard-deletes a
Meeting or its Recording objects.

Every deletion here follows the same order -- object storage first, database
row second -- and every storage step is idempotent (S3 DELETE on an
already-missing key is not an error), which is what makes a crash between
the two steps recoverable: the next scheduled sweep simply re-selects the
same still-present row, re-runs the (now no-op) storage delete, and finishes
the database delete it did not reach last time. There is no separate
reconciliation job -- the periodic sweep IS the reconciliation path. See
tests/test_purge_reconciliation.py.

Every function below that touches Meeting/Recording/artifact rows connects
via get_org_session(org_id) -- the least-privilege `meeting_app` role,
RLS-scoped to one organization on all fourteen tables (migrations
b3f2a1c9d4e7, c4d8e2f1a6b3). A candidate meeting_id from the wrong
organization is therefore invisible to the DELETE itself, not merely
unreachable in application code.
"""

from meeting_mgr.audit import record_audit
from meeting_mgr.db import get_org_session
from meeting_mgr.models import Recording
from meeting_mgr.storage import delete_object


def _purge_audio_objects(s, meeting_id: int) -> list[str]:
    """Delete this Meeting's audio objects from storage (idempotent) and
    return the keys removed, for the audit detail. Touches storage only --
    the caller deletes the Recording/Meeting row afterward, in the same
    get_org_session transaction."""
    rec = s.query(Recording).filter_by(meeting_id=meeting_id).one_or_none()
    if rec is None:
        return []
    keys = [k for k in (rec.raw_key, rec.normalized_key) if k]
    for key in keys:
        delete_object(key)
    return keys


def purge_meeting_audio(org_id: int, meeting_id: int) -> None:
    """Delete a Meeting's audio -- objects, then the Recording row -- leaving
    the Transcript and every derived artifact untouched (design spec §7:
    "citations still resolve to text, but click-to-hear-the-quote is
    unavailable"). Storage first: an orphaned DB row pointing at a gone key
    fails closed (GET /meetings/{id}/audio already 404s when there is no
    Recording row); an orphaned object with the DB row already gone would
    never be found again -- see issue #17.

    Deletes the whole Recording row rather than nulling raw_key/normalized_key
    (plan line 82): that is what makes select_purge_candidates()'s audio
    branch (INNER JOIN Recording, raw_key.isnot(None)) stop matching this
    Meeting on the next sweep -- no row, no join, not selected again."""
    with get_org_session(org_id) as s:
        keys = _purge_audio_objects(s, meeting_id)
        deleted = s.query(Recording).filter_by(meeting_id=meeting_id).delete()
        if deleted:
            record_audit(
                s,
                organization_id=org_id,
                actor_account_id=None,
                action="meeting.purge.audio",
                target=f"meeting:{meeting_id}",
                detail={"keys_deleted": len(keys)},
            )
