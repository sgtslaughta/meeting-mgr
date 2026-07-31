"""One place a name becomes a Participant.

The pipeline and the review UI must resolve a name the same way, or confirming
"Sarah" in the UI would create a second Sarah alongside the one extraction
already made.
"""

from sqlalchemy.exc import IntegrityError

from meeting_mgr.models import Participant


def resolve_participant(s, org_id: int, name: str | None) -> int | None:
    if not name or not name.strip():
        return None
    p = s.query(Participant).filter_by(organization_id=org_id, name=name).one_or_none()
    if p is not None:
        return p.id
    try:
        # SAVEPOINT: if a concurrent worker wins the race, only this insert
        # rolls back, not the session's other work.
        with s.begin_nested():
            p = Participant(organization_id=org_id, name=name)
            s.add(p)
        return p.id
    except IntegrityError:
        return s.query(Participant).filter_by(organization_id=org_id, name=name).one().id
