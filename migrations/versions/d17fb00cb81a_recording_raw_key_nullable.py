"""make recording.raw_key nullable

Revision ID: d17fb00cb81a
Revises: d5e9f3a2b8c1
Create Date: 2026-07-31

Task 4's select_purge_candidates() audio branch filters on
`Recording.raw_key.isnot(None)`, meaning "this Meeting still has an
un-purged audio object." Task 8 (real purge) is the sole writer that will
set `raw_key = NULL` after successfully deleting the storage object, so a
purged Meeting stops being reselected as an audio candidate on the next
sweep. That marker requires raw_key to be nullable; it was NOT NULL since
0001_initial, which meant the filter above was inert (always true) and an
audio-purged Meeting would have been reselected forever, silently starving
later genuine candidates out of every future bounded page.

Downgrade choice: re-imposing NOT NULL is only safe if no row has been
nulled yet (the common case: this migration reverted before Task 8 ever
runs, or before any org has an audio-retention policy). If any row *has*
been nulled, blindly restoring NOT NULL would either fail outright or
require fabricating a raw_key value for a Recording whose backing object
no longer exists -- both worse than refusing. downgrade() therefore
guards with a COUNT and raises if any row would violate the constraint,
rather than silently coercing or losing data; the operator must decide
manually (e.g. backfill a tombstone key) before downgrading past this
point.
"""
from alembic import op
import sqlalchemy as sa

revision = "d17fb00cb81a"
down_revision = "d5e9f3a2b8c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("recording", "raw_key", existing_type=sa.String(length=500), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    null_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM recording WHERE raw_key IS NULL")
    ).scalar()
    if null_count:
        raise RuntimeError(
            f"Cannot downgrade: {null_count} recording row(s) have raw_key IS NULL "
            "(audio already purged). Restoring NOT NULL would require fabricating a "
            "value for a deleted object. Backfill or delete those rows manually, "
            "then re-run the downgrade."
        )
    op.alter_column("recording", "raw_key", existing_type=sa.String(length=500), nullable=False)
