import itertools

import pytest
from fastapi import HTTPException

from meeting_mgr.authz import authorize
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Meeting, MeetingShare, Organization

# NOTE: the task brief's reference fixture used `id(a := object())` for email
# uniqueness. CPython reuses freed object addresses, so two accounts created
# in quick succession with the same role/org can collide and violate
# uq_account_org_email. Swapped for a monotonic counter to actually satisfy
# the global constraint of per-run unique emails.
_counter = itertools.count()


def _two_orgs(s):
    n = next(_counter)
    org_a = Organization(name=f"org-a-{id(s)}-{n}")
    org_b = Organization(name=f"org-b-{id(s)}-{n}")
    s.add_all([org_a, org_b])
    s.flush()
    return org_a.id, org_b.id


def _account(s, org_id, role="member"):
    a = Account(organization_id=org_id, email=f"{role}-{org_id}-{next(_counter)}@x.com", role=role)
    s.add(a)
    s.flush()
    return a


def _meeting(s, org_id, owner_id=None, visibility="private"):
    m = Meeting(organization_id=org_id, title="t", owner_account_id=owner_id, visibility=visibility)
    s.add(m)
    s.flush()
    return m


def test_cross_org_meeting_is_404_even_for_an_admin():
    with get_session() as s:
        org_a, org_b = _two_orgs(s)
        admin = _account(s, org_a, role="admin")
        meeting = _meeting(s, org_b, visibility="organization")
        with pytest.raises(HTTPException) as exc:
            authorize(admin, meeting, s)
        assert exc.value.status_code == 404


def test_owner_can_read_their_own_private_meeting():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="private")
        authorize(owner, meeting, s)  # must not raise


def test_a_different_member_cannot_read_a_private_meeting():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        other = _account(s, org_id)
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="private")
        with pytest.raises(HTTPException) as exc:
            authorize(other, meeting, s)
        assert exc.value.status_code == 404


def test_shared_meeting_is_visible_only_to_the_named_account():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        shared_with = _account(s, org_id)
        stranger = _account(s, org_id)
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="shared")
        s.add(MeetingShare(meeting_id=meeting.id, account_id=shared_with.id))
        s.flush()

        authorize(shared_with, meeting, s)  # must not raise
        with pytest.raises(HTTPException) as exc:
            authorize(stranger, meeting, s)
        assert exc.value.status_code == 404


def test_organization_visibility_is_readable_by_any_member():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        colleague = _account(s, org_id)
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="organization")
        authorize(colleague, meeting, s)  # must not raise


def test_auditor_can_read_a_private_meeting_they_do_not_own():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        auditor = _account(s, org_id, role="auditor")
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="private")
        authorize(auditor, meeting, s)  # not a Visibility grant, but auditor still reads all


def test_auditor_cannot_write_even_to_a_meeting_they_can_read():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        auditor = _account(s, org_id, role="auditor")
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="organization")
        with pytest.raises(HTTPException) as exc:
            authorize(auditor, meeting, s, write=True)
        assert exc.value.status_code == 403


def test_owner_can_write_their_own_meeting():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id)
        meeting = _meeting(s, org_id, owner_id=owner.id, visibility="private")
        authorize(owner, meeting, s, write=True)  # must not raise


def test_missing_meeting_is_404():
    with get_session() as s:
        org_id, _ = _two_orgs(s)
        member = _account(s, org_id)
        with pytest.raises(HTTPException) as exc:
            authorize(member, None, s)
        assert exc.value.status_code == 404


def test_readable_meetings_filter_agrees_with_can_read():
    # This is the test that keeps the two encodings of the read rule honest:
    # for every Role and a mix of ownership/Visibility/share combinations,
    # the Meetings `readable_meetings_filter()` returns must be exactly the
    # set `_can_read()` approves one at a time. If either encoding drifts
    # from the rule stated in authz.py's module docstring, this fails.
    from meeting_mgr.authz import _can_read, readable_meetings_filter

    with get_session() as s:
        org_id, _ = _two_orgs(s)
        owner = _account(s, org_id, role="member")
        other_member = _account(s, org_id, role="member")
        admin = _account(s, org_id, role="admin")
        auditor = _account(s, org_id, role="auditor")

        private_owned = _meeting(s, org_id, owner_id=owner.id, visibility="private")
        private_other = _meeting(s, org_id, owner_id=other_member.id, visibility="private")
        org_visible = _meeting(s, org_id, owner_id=other_member.id, visibility="organization")
        shared_with_owner = _meeting(s, org_id, owner_id=other_member.id, visibility="shared")
        s.add(MeetingShare(meeting_id=shared_with_owner.id, account_id=owner.id))
        s.flush()

        all_meetings = [private_owned, private_other, org_visible, shared_with_owner]

        for account in (owner, other_member, admin, auditor):
            expected = {m.id for m in all_meetings if _can_read(account, m, s)}
            actual = {
                m.id
                for m in s.query(Meeting)
                .filter(Meeting.id.in_([m.id for m in all_meetings]))
                .filter(readable_meetings_filter(account))
                .all()
            }
            assert actual == expected, f"role={account.role} account_id={account.id}"


def test_cross_org_share_does_not_grant_access():
    """A MeetingShare row naming an account in a *different* organization
    from the meeting must not grant access. MeetingShare has no
    organization_id and nothing FK/CHECK/trigger-enforces that the shared
    account's org matches the meeting's org, so authorize() itself must
    reject on the organization_id mismatch before the share is ever
    consulted. Kill: delete the `meeting.organization_id != account.organization_id`
    check (or reorder it after the share lookup) and this test starts
    raising nothing / raising a 200-equivalent instead of 404."""
    with get_session() as s:
        org_a, org_b = _two_orgs(s)
        owner = _account(s, org_a)
        outsider = _account(s, org_b)
        meeting = _meeting(s, org_a, owner_id=owner.id, visibility="shared")
        s.add(MeetingShare(meeting_id=meeting.id, account_id=outsider.id))
        s.flush()

        with pytest.raises(HTTPException) as exc:
            authorize(outsider, meeting, s)
        assert exc.value.status_code == 404


def test_admin_of_one_org_cannot_read_meeting_in_another_org():
    """admin has full access within its own organization only. Kill: remove
    the organization_id check (or move the role in ("admin","auditor")
    branch of _can_read before the tenancy gate in authorize()) and this
    test starts passing authorize() for a cross-org admin."""
    with get_session() as s:
        org_a, org_b = _two_orgs(s)
        admin_a = _account(s, org_a, role="admin")
        meeting_b = _meeting(s, org_b, visibility="private")
        with pytest.raises(HTTPException) as exc:
            authorize(admin_a, meeting_b, s)
        assert exc.value.status_code == 404


def test_readable_meetings_filter_excludes_cross_org_meeting_for_admin():
    """readable_meetings_filter() does not itself scope by organization_id
    (callers must), so this test documents/enforces that expectation at the
    authz.py layer: an admin's filter alone returns True for meetings in
    ANY org unless the caller also filters by organization_id. This is not
    a defect in readable_meetings_filter() -- it's the documented contract
    -- but callers (list_meetings) MUST additionally filter by
    account.organization_id. Kill: this test fails if readable_meetings_filter
    stops returning a permissive True for admin/auditor roles."""
    from meeting_mgr.authz import readable_meetings_filter

    with get_session() as s:
        org_a, org_b = _two_orgs(s)
        admin = _account(s, org_a, role="admin")
        meeting_b = _meeting(s, org_b, visibility="private")

        # Without an organization_id filter, readable_meetings_filter() alone
        # would include the other org's meeting -- proving callers MUST add
        # the org scope themselves, as documented in authz.py.
        unscoped = (
            s.query(Meeting)
            .filter(Meeting.id == meeting_b.id)
            .filter(readable_meetings_filter(admin))
            .all()
        )
        assert unscoped != []

        scoped = (
            s.query(Meeting)
            .filter(Meeting.id == meeting_b.id)
            .filter(Meeting.organization_id == admin.organization_id)
            .filter(readable_meetings_filter(admin))
            .all()
        )
        assert scoped == []


def test_require_role_rejects_role_not_in_allowed():
    from meeting_mgr.authz import require_role

    with get_session() as s:
        org_id, _ = _two_orgs(s)
        member = _account(s, org_id, role="member")
        with pytest.raises(HTTPException) as exc:
            require_role(member, frozenset({"admin", "auditor"}))
        assert exc.value.status_code == 403


def test_require_role_allows_role_in_allowed():
    from meeting_mgr.authz import require_role

    with get_session() as s:
        org_id, _ = _two_orgs(s)
        auditor = _account(s, org_id, role="auditor")
        require_role(auditor, frozenset({"admin", "auditor"}))  # must not raise
