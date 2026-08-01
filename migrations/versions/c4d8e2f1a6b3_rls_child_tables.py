"""enable row-level security for child artifact tables

Revision ID: c4d8e2f1a6b3
Revises: b3f2a1c9d4e7
Create Date: 2026-07-31

Closes the gap tracked in issue #35: the five directly-tenanted tables
(organization, meeting, participant, account, audit_log_entry) got RLS
policies in b3f2a1c9d4e7, but the child artifact tables -- which carry only
meeting_id, not organization_id -- did not.

Rather than denormalising organization_id onto every child table, each
policy here scopes through meeting_id via a subquery against `meeting`,
which is itself RLS-filtered by its own tenant_isolation policy. Postgres
composes these correctly: the subquery in the child policy only ever sees
the rows `meeting`'s own policy would allow, so tenancy is inherited rather
than duplicated. Verified empirically (including a two-level chain) before
relying on this -- see the RLS child-tables report.

`attribution` has no meeting_id column of its own; it reaches `meeting`
only via `speaker_cluster.meeting_id`, so its policy chains through
speaker_cluster (which is itself scoped by the same mechanism).
"""

from alembic import op

revision = "c4d8e2f1a6b3"
down_revision = "b3f2a1c9d4e7"
branch_labels = None
depends_on = None

# Tables scoped directly by their own meeting_id column.
_MEETING_SCOPED_TABLES = (
    "segment",
    "key_topic",
    "minute",
    "action_item",
    "decision_point",
    "speaker_cluster",
    "recording",
    "meeting_share",
)


def upgrade() -> None:
    for table in _MEETING_SCOPED_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (meeting_id IN (SELECT id FROM meeting))"
        )

    # attribution has no meeting_id; it hangs off speaker_cluster instead,
    # which is itself meeting-scoped by the policy created above.
    op.execute("ALTER TABLE attribution ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON attribution "
        "USING (cluster_id IN (SELECT id FROM speaker_cluster))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON attribution")
    op.execute("ALTER TABLE attribution DISABLE ROW LEVEL SECURITY")

    for table in reversed(_MEETING_SCOPED_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
