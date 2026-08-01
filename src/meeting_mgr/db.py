from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from meeting_mgr.config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session():
    """Read-write session. Commits on clean exit, rolls back on exception.

    Connects as the superuser/owner, so RLS does NOT apply. Reserved for the
    pipeline, Celery, and the three identity-bootstrap call sites (login,
    oidc_callback, get_current_account) -- everything else should use the
    org-scoped sessions below.
    """
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
    """Read-only session. Always rolls back, so a stray write cannot commit.

    Same untenanted, RLS-bypassing connection as get_session() -- pipeline,
    Celery, and identity-bootstrap sites only.
    """
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# Org-scoped sessions connect as the least-privilege `meeting_app` role (see
# migration xxxx_enable_rls) rather than the superuser `engine` above, which
# is what makes the tenant_isolation RLS policies actually apply -- Postgres
# exempts superusers and table owners from RLS by default.
org_engine = create_engine(get_settings().database_url_app, pool_pre_ping=True)
OrgSessionLocal = sessionmaker(bind=org_engine, expire_on_commit=False)


def _set_org_id(s, org_id: int) -> None:
    # NOTE: plain `SET LOCAL app.org_id = :org_id` does not accept a bound
    # parameter -- Postgres's SET grammar only allows a literal/identifier in
    # that position, not `$1`, and raises a syntax error. `set_config(...)` is
    # an ordinary function call, so it takes a normal bind parameter; passing
    # is_local=true gives it the same "resets at commit/rollback" behaviour
    # as SET LOCAL.
    s.execute(text("SELECT set_config('app.org_id', :org_id, true)"), {"org_id": str(org_id)})


@contextmanager
def get_org_session(org_id: int):
    """Read-write session scoped to one Organization by Postgres RLS.

    Connects as the least-privilege `meeting_app` role and sets the
    `app.org_id` GUC that every tenant_isolation policy checks. Defence in
    depth behind authz.authorize() -- not a replacement for it, since RLS has
    no notion of Visibility or Role.

    The GUC is set with is_local=true (equivalent to SET LOCAL), so it is
    scoped to the current transaction and resets to '' at commit/rollback --
    not back to a "never set" NULL, verified empirically (see the NULLIF in
    the tenant_isolation policy). That reset happens on *every* transaction
    boundary regardless of whether the physical connection is later reused by
    the pool, and every call here re-sets app.org_id before running any
    query, which together are what keep one request's org_id from leaking
    into the next request's pooled connection.
    """
    s = OrgSessionLocal()
    try:
        _set_org_id(s, org_id)
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def get_readonly_org_session(org_id: int):
    """Read-only counterpart of get_org_session. Always rolls back."""
    s = OrgSessionLocal()
    try:
        _set_org_id(s, org_id)
        yield s
    finally:
        s.rollback()
        s.close()
