from sqlalchemy import text
from meeting_mgr.db import get_session, get_readonly_session

def test_session_connects():
    with get_session() as s:
        assert s.execute(text("select 1")).scalar() == 1

def test_readonly_session_discards_writes():
    with get_session() as s:
        s.execute(text("create table if not exists probe (id int)"))
        s.execute(text("delete from probe"))
    with get_readonly_session() as s:
        s.execute(text("insert into probe values (1)"))
    with get_session() as s:
        assert s.execute(text("select count(*) from probe")).scalar() == 0
