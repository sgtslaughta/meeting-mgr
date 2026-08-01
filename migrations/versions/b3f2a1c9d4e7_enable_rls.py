"""enable row-level security for tenant isolation

Revision ID: b3f2a1c9d4e7
Revises: 77e274d80c90
Create Date: 2026-07-31
"""

from alembic import op

revision = "b3f2a1c9d4e7"
down_revision = "77e274d80c90"
branch_labels = None
depends_on = None

_DIRECT_TABLES = ("organization", "meeting", "participant", "account", "audit_log_entry")


def upgrade() -> None:
    # Least-privilege role the API connects as for authenticated requests.
    # It is NOT the table owner, which is what makes RLS apply to it at all
    # (Postgres exempts the owner and superusers by default).
    op.execute(
        "DO $$ BEGIN "
        "  CREATE ROLE meeting_app LOGIN PASSWORD 'meeting_app'; "
        "EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO meeting_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO meeting_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO meeting_app")

    for table in _DIRECT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # organization itself has no organization_id column -- its own id IS
        # the tenant key.
        key = "id" if table == "organization" else "organization_id"
        # NULLIF(..., '') matters, not just style: once a pooled connection's
        # app.org_id placeholder has been set at all, Postgres resets it to
        # '' (not NULL) after the setting transaction commits/rolls back --
        # verified empirically. A bare `current_setting(...)::int` cast then
        # raises a DataError on the next query instead of the intended
        # default-deny (0 rows). NULLIF folds '' back to NULL first, so the
        # comparison is NULL and the row is excluded cleanly either way.
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({key} = NULLIF(current_setting('app.org_id', true), '')::int)"
        )


def downgrade() -> None:
    for table in _DIRECT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    # meeting_app was never the owner of any object, but the GRANTs above
    # register ACL dependencies in pg_shdepend -- DROP ROLE fails on those
    # ("role ... cannot be dropped because some objects depend on it") unless
    # they are revoked (or otherwise cleared) first.
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM meeting_app")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM meeting_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM meeting_app")
    op.execute("DROP OWNED BY meeting_app")
    op.execute("DROP ROLE IF EXISTS meeting_app")
