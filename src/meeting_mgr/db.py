import functools
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from meeting_mgr.config import get_settings


class Base(DeclarativeBase):
    pass


@functools.lru_cache(maxsize=1)
def _get_engine():
    """Owner/superuser engine -- RLS-bypassing. Built on first use, not at
    import, so `import meeting_mgr.db` (e.g. from models/*.py, for Base)
    never opens a connection on its own."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@functools.lru_cache(maxsize=1)
def _get_session_local():
    return sessionmaker(bind=_get_engine(), expire_on_commit=False)


@functools.lru_cache(maxsize=1)
def _get_org_engine():
    """Least-privilege `meeting_app` engine -- the one RLS policies actually
    apply to. See the module docstring on get_org_session for why this must
    stay a genuinely different role from _get_engine(), never the same URL."""
    return create_engine(get_settings().database_url_app, pool_pre_ping=True)


@functools.lru_cache(maxsize=1)
def _get_org_session_local():
    return sessionmaker(bind=_get_org_engine(), expire_on_commit=False)


def __getattr__(name):
    # PEP 562 module __getattr__: keeps `engine` / `org_engine` /
    # `SessionLocal` / `OrgSessionLocal` importable exactly as before
    # (`from meeting_mgr.db import engine`, `db.org_engine`, ...), but only
    # builds the underlying engine the first time one of those names is
    # actually touched -- not merely on `import meeting_mgr.db`.
    if name == "engine":
        return _get_engine()
    if name == "org_engine":
        return _get_org_engine()
    if name == "SessionLocal":
        return _get_session_local()
    if name == "OrgSessionLocal":
        return _get_org_session_local()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@contextmanager
def get_session():
    """Read-write session. Commits on clean exit, rolls back on exception.

    Connects as the superuser/owner, so RLS does NOT apply. Reserved for the
    pipeline, Celery, and the four identity-bootstrap call sites (login,
    oidc_callback, get_current_account, get_bot_credential) -- everything
    else should use the org-scoped sessions below.
    """
    s = _get_session_local()()
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
    Celery, and the four identity-bootstrap sites only.
    """
    s = _get_session_local()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


# Org-scoped sessions connect as the least-privilege `meeting_app` role (see
# migration xxxx_enable_rls) rather than the superuser `engine` above, which
# is what makes the tenant_isolation RLS policies actually apply -- Postgres
# exempts superusers and table owners from RLS by default. See _get_org_engine
# above; org_engine/OrgSessionLocal remain importable via module __getattr__.


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
    s = _get_org_session_local()()
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
    s = _get_org_session_local()()
    try:
        _set_org_id(s, org_id)
        yield s
    finally:
        s.rollback()
        s.close()
