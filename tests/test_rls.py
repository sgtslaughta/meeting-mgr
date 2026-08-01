import uuid

from sqlalchemy import text

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Meeting, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def test_org_scoped_session_only_sees_its_own_org_meetings():
    org_a, org_b = _org(), _org()
    with get_session() as s:
        s.add(Meeting(organization_id=org_a, title="a"))
        s.add(Meeting(organization_id=org_b, title="b"))

    # A raw, unfiltered SELECT — no organization_id predicate at all — must
    # still be confined to org_a by the database itself, not by this query.
    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT title FROM meeting")).fetchall()
        titles = {r[0] for r in rows}
    assert "a" in titles
    assert "b" not in titles, "RLS did not confine the query to its own organization"


def test_org_scoped_session_cannot_see_a_different_org_row_by_direct_lookup():
    org_a, org_b = _org(), _org()
    with get_session() as s:
        m = Meeting(organization_id=org_b, title="secret")
        s.add(m)
        s.flush()
        mid = m.id

    with get_org_session(org_a) as s:
        row = s.get(Meeting, mid)
    assert row is None, "a direct primary-key lookup must not bypass RLS"


def test_tenant_not_set_sees_no_rows():
    """No app.org_id set on the connection -> default-deny, not default-allow."""
    org_a = _org()
    with get_session() as s:
        s.add(Meeting(organization_id=org_a, title="unset-tenant-probe"))

    from meeting_mgr.db import OrgSessionLocal

    s = OrgSessionLocal()
    try:
        rows = s.execute(text("SELECT title FROM meeting")).fetchall()
    finally:
        s.rollback()
        s.close()
    titles = {r[0] for r in rows}
    assert "unset-tenant-probe" not in titles, "unset app.org_id must see zero rows, not all rows"


def test_tenant_switch_on_same_connection_does_not_leak():
    """SET LOCAL resets at transaction end, so a pooled connection reused for
    org B after org A must not still see org A's rows. get_org_session opens
    a fresh logical Session each call but the pool may hand back the same
    physical connection -- exercise that reuse path directly."""
    org_a, org_b = _org(), _org()
    with get_session() as s:
        s.add(Meeting(organization_id=org_a, title="only-a"))
        s.add(Meeting(organization_id=org_b, title="only-b"))

    from meeting_mgr.db import org_engine

    conn = org_engine.connect()
    try:
        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_a)}
            )
            titles_a = {r[0] for r in conn.execute(text("SELECT title FROM meeting")).fetchall()}

        with conn.begin():
            conn.execute(
                text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_b)}
            )
            titles_b = {r[0] for r in conn.execute(text("SELECT title FROM meeting")).fetchall()}
    finally:
        conn.close()

    assert "only-a" in titles_a and "only-b" not in titles_a
    assert "only-b" in titles_b and "only-a" not in titles_b
