"""add unique constraint on bot_credential(organization_id, label)

Revision ID: b8c2e6f0a1d3
Revises: f4b8d1e6a9c2
Create Date: 2026-08-01

Issue #39: nothing stopped two bot credentials in one organization sharing
a label. Cosmetic/UX only -- routing keys off organization_id and
bot_credential_id, never the label -- but a future admin UI listing
credentials by label deserves the same guarantee watch_folder already has
via uq_watch_folder_org_path (e7a1c3f9b2d4). Same shape here:
UniqueConstraint("organization_id", "label").

Pre-existing duplicates: the shared dev/test database already has rows
with duplicate (organization_id, label) pairs from earlier test runs (e.g.
many orgs each with two rows labeled "a"). A unique constraint that cannot
be applied to existing data is a migration that fails in production, so
upgrade() first deduplicates in place: for every (organization_id, label)
group, the earliest-created row (lowest id) keeps its label; every other
row in the group is renamed to "{label}-dup-{id}" before the constraint is
added. This is a one-time, deterministic, idempotent-on-empty-input rename
that never touches routing (bot_credential_id/organization_id), so no
behavior other than the display label of pre-existing duplicate rows
changes. Branches off f4b8d1e6a9c2 (add_bot_session), the actual repo head
as of this plan.
"""

import sqlalchemy as sa
from alembic import op

revision = "b8c2e6f0a1d3"
down_revision = "f4b8d1e6a9c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE bot_credential AS bc
        SET label = bc.label || '-dup-' || bc.id
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY organization_id, label ORDER BY id
                   ) AS rn
            FROM bot_credential
        ) AS ranked
        WHERE bc.id = ranked.id AND ranked.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_bot_credential_org_label", "bot_credential", ["organization_id", "label"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_bot_credential_org_label", "bot_credential", type_="unique")
