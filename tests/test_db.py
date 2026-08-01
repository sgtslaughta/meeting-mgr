from sqlalchemy import text

from meeting_mgr.db import get_org_session, get_readonly_session, get_session


def test_session_connects():
    with get_session() as s:
        assert s.execute(text("select 1")).scalar() == 1


def test_readonly_session_discards_writes():
    # Drop the probe table afterwards: a committed table with no model makes
    # every later `alembic revision --autogenerate` propose dropping it.
    try:
        with get_session() as s:
            s.execute(text("create table if not exists probe (id int)"))
            s.execute(text("delete from probe"))
        with get_readonly_session() as s:
            s.execute(text("insert into probe values (1)"))
        with get_session() as s:
            assert s.execute(text("select count(*) from probe")).scalar() == 0
    finally:
        with get_session() as s:
            s.execute(text("drop table if exists probe"))


def test_engine_and_org_engine_connect_as_different_roles():
    # This is the property a careless "make both lazy" fix can silently
    # destroy by pointing both engines at the same URL/role -- which would
    # make current_user equal and disable RLS tenant isolation everywhere.
    with get_session() as s:
        owner_role = s.execute(text("SELECT current_user")).scalar()
    with get_org_session(org_id=1) as s:
        app_role = s.execute(text("SELECT current_user")).scalar()
    assert owner_role != app_role
    assert app_role == "meeting_app"


def test_engine_and_org_engine_are_cached_singletons():
    import meeting_mgr.db as db

    # If engine/org_engine were built fresh on every access instead of
    # cached, these identity checks would fail.
    assert db.engine is db.engine
    assert db.org_engine is db.org_engine
    assert db.engine is not db.org_engine
