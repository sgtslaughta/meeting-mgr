"""add retention_policy table, RLS policy, and meeting_app grants

Revision ID: d5e9f3a2b8c1
Revises: c4d8e2f1a6b3
Create Date: 2026-08-01

retention_policy carries organization_id directly, same shape as the five
tables b3f2a1c9d4e7 scoped by their own organization_id column. It gets the
same policy shape as that migration, NOT the meeting-id-subquery shape
c4d8e2f1a6b3 used for child artifact tables.

b3f2a1c9d4e7's `GRANT ... ON ALL TABLES IN SCHEMA public TO meeting_app` was
a one-time snapshot over the tables that existed when it ran -- it does not
cover a table created afterward. Skipping the explicit GRANT below would
make every get_org_session() query against this table fail with a Postgres
permission error, not a (misleadingly reassuring) empty RLS result.
"""
from alembic import op
import sqlalchemy as sa

revision = "d5e9f3a2b8c1"
down_revision = "c4d8e2f1a6b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "retention_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("audio_retention_days", sa.Integer(), nullable=True),
        sa.Column("meeting_retention_days", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.UniqueConstraint("organization_id", name="uq_retention_policy_org"),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON retention_policy TO meeting_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE retention_policy_id_seq TO meeting_app")

    op.execute("ALTER TABLE retention_policy ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON retention_policy "
        "USING (organization_id = NULLIF(current_setting('app.org_id', true), '')::int)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON retention_policy")
    op.execute("ALTER TABLE retention_policy DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL PRIVILEGES ON retention_policy FROM meeting_app")
    op.execute("REVOKE ALL PRIVILEGES ON SEQUENCE retention_policy_id_seq FROM meeting_app")
    op.drop_table("retention_policy")
