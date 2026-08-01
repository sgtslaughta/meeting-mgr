"""Shared chunk-key helpers for capture.py and bot.py -- issue #38.

Both ingest paths store raw upload chunks under `raw/{meeting_id}/<subdir>/`,
zero-padded and suffixed, then list/sort them the same way to build a
manifest. pipeline/watch.py does NOT participate: it ingests one whole file
per Meeting with no chunk/manifest concept at all, so it has no use for this
module.

The `{seq:06d}` zero-padding is the ordering guard that matters for storage
listing (so a lexicographic S3 listing roughly tracks upload order), but
every caller sorts on the parsed integer from chunk_seq(), not on key string
order -- so it is the *combination* that guarantees correct order past 10**6
chunks, and either guard alone (padding without integer-sort, or
integer-sort without padding) already gets it right below that. Keep both;
do not simplify away either one.
"""


def chunk_prefix(meeting_id: int, subdir: str) -> str:
    return f"raw/{meeting_id}/{subdir}/"


def chunk_key(meeting_id: int, seq: int, subdir: str, suffix: str) -> str:
    return f"{chunk_prefix(meeting_id, subdir)}{seq:06d}{suffix}"


def chunk_seq(prefix: str, suffix: str, key: str) -> int:
    return int(key.removeprefix(prefix).removesuffix(suffix))
