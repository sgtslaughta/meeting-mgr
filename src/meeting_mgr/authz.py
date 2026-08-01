"""The single authorization chokepoint (design spec §6).

Two independent axes: Role (org-wide standing: admin/member/auditor) and
Visibility (per-Meeting reach: private/shared/organization). An auditor's
read access is NOT a Visibility grant — it is evaluated on its own branch
below, deliberately before Visibility is consulted at all.

Every endpoint that touches a Meeting must call authorize() (single-Meeting)
or readable_meetings_filter() (list of Meetings) and nothing else for this
purpose; every endpoint whose authorization decision is not Meeting-scoped
must call require_role() and nothing else. A second hand-rolled org/role
comparison anywhere else in the codebase is a defect, not redundancy: it is
a second place authorization can be wrong.

One deliberate exception: the bot ingest endpoints in api/bot.py touch
Meetings but call neither authorize() nor require_role(). A bot is not an
Account, so there is no principal for either to evaluate. Their
authorization is get_bot_credential() (auth/bot_deps.py): a valid,
unrevoked credential may act only within its own organization, and every
organization_id they write comes from the resolved credential row rather
than from client input. This is the only sanctioned bypass — an audit that
greps for "every Meeting endpoint calls authorize()" should expect exactly
these routes and nothing else.

_can_read() and readable_meetings_filter() are two encodings of the SAME
read-visibility rule — one evaluated per-Meeting in Python, one compiled to
a SQL predicate for filtering a list. They are kept adjacent here
deliberately and MUST change together; test_readable_meetings_filter_agrees_
with_can_read in test_authz.py fails if they diverge.
"""

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from meeting_mgr.models import Account, Meeting, MeetingShare


def _can_read(account: Account, meeting: Meeting, s: Session) -> bool:
    if account.role in ("admin", "auditor"):
        return True
    if meeting.owner_account_id == account.id:
        return True
    if meeting.visibility == "organization":
        return True
    if meeting.visibility == "shared":
        return (
            s.query(MeetingShare).filter_by(meeting_id=meeting.id, account_id=account.id).first()
            is not None
        )
    return False


def readable_meetings_filter(account: Account) -> ColumnElement[bool]:
    """The list-shaped encoding of `_can_read()` — see module docstring.

    Does NOT scope by `organization_id`; callers filter to
    `account.organization_id` (or connect via `get_readonly_org_session`)
    separately, same as any other query.
    """
    if account.role in ("admin", "auditor"):
        return Meeting.id.isnot(None)  # unconditionally true, same shape as a real predicate
    return or_(
        Meeting.owner_account_id == account.id,
        Meeting.visibility == "organization",
        Meeting.id.in_(_session_scoped_shared_meeting_ids_subquery(account)),
    )


def _session_scoped_shared_meeting_ids_subquery(account: Account):
    """Correlated subquery of MeetingShare.meeting_id for this account, used
    by readable_meetings_filter() so the whole filter stays a single
    expression composable with .filter(...) without a separate query."""
    from sqlalchemy import select

    return select(MeetingShare.meeting_id).where(MeetingShare.account_id == account.id)


def require_role(account: Account, allowed: frozenset[str]) -> None:
    """Raise 403 if `account.role` is not in `allowed`.

    For authorization decisions with no Meeting/Visibility axis at all
    (e.g. "may this Role read the Audit Log"). Meeting-scoped decisions use
    authorize() or readable_meetings_filter() instead, never this.
    """
    if account.role not in allowed:
        raise HTTPException(403, f"requires one of roles: {sorted(allowed)}")


def authorize(
    account: Account, meeting: Meeting | None, s: Session, *, write: bool = False
) -> None:
    """Raise if `account` may not act on `meeting`.

    A tenant mismatch or a missing read grant is reported as 404, not 403, so
    the error code cannot be used to enumerate meetings in other
    Organizations or other Accounts' private meetings. A write refusal for an
    auditor who CAN read the meeting is reported as 403: that distinction is
    real, user-facing signal ("you may not change this"), not something to
    hide behind a generic not-found.
    """
    if meeting is None or meeting.organization_id != account.organization_id:
        raise HTTPException(404, "meeting not found")
    if not _can_read(account, meeting, s):
        raise HTTPException(404, "meeting not found")
    if write and account.role == "auditor":
        raise HTTPException(403, "auditor role is read-only")
