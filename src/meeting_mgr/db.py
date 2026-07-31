from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from meeting_mgr.config import get_settings

class Base(DeclarativeBase):
    pass

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

@contextmanager
def get_session():
    """Read-write session. Commits on clean exit, rolls back on exception."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

@contextmanager
def get_readonly_session():
    """Read-only session. Always rolls back, so a stray write cannot commit."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
