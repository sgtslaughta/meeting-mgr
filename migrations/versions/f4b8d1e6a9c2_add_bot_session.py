"""add bot_session table, RLS policy, cascade FK, and meeting_app grants

Revision ID: f4b8d1e6a9c2
Revises: a2c7f4e91b3d
Create Date: 2026-08-01

bot_session carries organization_id directly, same shape as bot_credential
(a2c7f4e91b3d) and watch_folder (e7a1c3f9b2d4) -- NOT the meeting-id-subquery
shape c4d8e2f1a6b3 used for child artifact tables, even though this table
also has a meeting_id column: organization_id is included directly here
(unlike the c4d8e2f1a6b3 tables) so RLS does not need a join, and because a
BotSession is conceptually closer to a per-session config/tracking row (like
watch_folder) than to a Transcript-derived artifact.

meeting_id has ondelete="CASCADE": purge_meeting_full() (pipeline/purge.py,
Phase 4) relies on every child table cascading from meeting.id so that
deleting the Meeting row alone is sufficient -- see its docstring, which
cites Postgres's documented behaviour that referential-integrity actions,
including ON DELETE CASCADE, always bypass row security. Without this FK
option, a purged Meeting would leave an orphaned BotSession row referencing
a meeting_id that no longer exists.
"""

from alembic import op
import sqlalchemy as sa

revision = "f4b8d1e6a9c2"
down_revision = "a2c7f4e91b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("bot_credential_id", sa.Integer(), nullable=False),
        sa.Column("meeting_id", sa.Integer(), nullable=False),
        sa.Column("platform_meeting_id", sa.String(300), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organization.id"]),
        sa.ForeignKeyConstraint(["bot_credential_id"], ["bot_credential.id"]),
        sa.ForeignKeyConstraint(["meeting_id"], ["meeting.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("meeting_id", name="uq_bot_session_meeting"),
        sa.UniqueConstraint(
            "bot_credential_id", "platform_meeting_id", name="uq_bot_session_credential_platform"
        ),
    )

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON bot_session TO meeting_app")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE bot_session_id_seq TO meeting_app")

    op.execute("ALTER TABLE bot_session ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON bot_session "
        "USING (organization_id = NULLIF(current_setting('app.org_id', true), '')::int)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON bot_session")
    op.execute("ALTER TABLE bot_session DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL PRIVILEGES ON bot_session FROM meeting_app")
    op.execute("REVOKE ALL PRIVILEGES ON SEQUENCE bot_session_id_seq FROM meeting_app")
    op.drop_table("bot_session")
