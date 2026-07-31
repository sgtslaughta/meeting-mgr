"""The single place provenance is promoted.

ADR-0003 makes provenance the signal that separates a machine guess from a
human decision. Keeping exactly one writer of "confirmed" means there is
exactly one place to audit when that guarantee is questioned.
"""


def confirm(row) -> None:
    """Mark a derived fact as decided by a human. Idempotent."""
    row.provenance = "confirmed"
