from sqlalchemy import text
from meeting_mgr.db import get_session

def test_session_connects():
    with get_session() as s:
        assert s.execute(text("select 1")).scalar() == 1
