"""add watch_folder table, RLS policy, and meeting_app grants

Revision ID: e7a1c3f9b2d4
Revises: d17fb00cb81a
Create Date: 2026-08-01

watch_folder carries organization_id directly, same shape as
retention_policy (d5e9f3a2b8c1) -- same tenant_isolation policy shape,
NOT the meeting-id-subquery shape c4d8e2f1a6b3 used for child artifact
tables. b3f2a1c9d4e7's snapshot GRANT does not cover this table; skipping
the explicit GRANT below fails every get_org_session() query against it
with a Postgres permission error, not an RLS-empty-result. Branches off
d17fb00cb81a (recording_raw_key_nullable), the actual repo head as of
this plan -- not its parent d5e9f3a2b8c1 -- so `alembic upgrade head`
does not hit "Multiple head revisions are present."
"""

import sqlalchemy as sa
from alembic import op

revision = "e7a1c3f9b2d4"
down_revision = "d17fb00cb81a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "watch_folder",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("root_path", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_scan_at", sa.DateTime(), nullable=True),
        sa.Column("last_scan_error", sa.String(2000), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["owner_account_id"], ["account.id"]),
        sa.UniqueConstraint("organization_id", "root_path", name="uq_watch_folder_org_path"),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON watch_folder TO meeting_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE watch_folder_id_seq TO meeting_app")

    op.execute("ALTER TABLE watch_folder ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON watch_folder "
        "USING (organization_id = NULLIF(current_setting('app.org_id', true), '')::int)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON watch_folder")
    op.execute("ALTER TABLE watch_folder DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL PRIVILEGES ON watch_folder FROM meeting_app")
    op.execute("REVOKE ALL PRIVILEGES ON SEQUENCE watch_folder_id_seq FROM meeting_app")
    op.drop_table("watch_folder")
