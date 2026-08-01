"""add bot_credential table, RLS policy, and meeting_app grants

Revision ID: a2c7f4e91b3d
Revises: e7a1c3f9b2d4
Create Date: 2026-08-01

bot_credential carries organization_id directly, same shape as
watch_folder (e7a1c3f9b2d4) and retention_policy (d5e9f3a2b8c1) -- same
tenant_isolation policy shape, NOT the meeting-id-subquery shape
c4d8e2f1a6b3 used for child artifact tables. b3f2a1c9d4e7's snapshot GRANT
does not cover this table; skipping the explicit GRANT below fails every
get_org_session() query against it with a Postgres permission error, not
an RLS-empty-result. Branches off e7a1c3f9b2d4 (add_watch_folder), the
actual repo head as of this plan.
"""

import sqlalchemy as sa
from alembic import op

revision = "a2c7f4e91b3d"
down_revision = "e7a1c3f9b2d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_credential",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("owner_account_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(200), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["owner_account_id"], ["account.id"]),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bot_credential TO meeting_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE bot_credential_id_seq TO meeting_app")

    op.execute("ALTER TABLE bot_credential ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON bot_credential "
        "USING (organization_id = NULLIF(current_setting('app.org_id', true), '')::int)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON bot_credential")
    op.execute("ALTER TABLE bot_credential DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL PRIVILEGES ON bot_credential FROM meeting_app")
    op.execute("REVOKE ALL PRIVILEGES ON SEQUENCE bot_credential_id_seq FROM meeting_app")
    op.drop_table("bot_credential")
