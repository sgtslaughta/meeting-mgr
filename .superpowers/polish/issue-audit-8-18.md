# Issue audit: #8-#18 (main branch, 2026-08-01)

## #8 — Test coverage: alignment against multi-span/empty-spans clusters
STILL OPEN. `tests/test_align.py` has `test_align_assigns_best_overlapping_cluster`
(single-span clusters), `test_overlap_is_never_negative`, and
`test_align_breaks_an_exact_tie_deterministically`. No test constructs a
`SpeakerCluster` with `spans=[...]` containing more than one span, and none
with `spans=[]`/`None` reaching `align()` (the tie test uses single-span
clusters). `align()` at `src/meeting_mgr/pipeline/align.py:16` does
`c.spans or []` and sums overlap across all spans — plausible-correct but
unpinned.

## #9 — Test coverage: Participant dedup across two clusters, same proposed name
STILL OPEN. `tests/test_attribute.py` covers single-cluster attribution,
skip-on-null, skip-on-whitespace. No test proposes the same name for two
different `SpeakerCluster` rows and asserts they resolve to one `Participant`.
`resolve_participant` (`src/meeting_mgr/participants.py:15`) implements the
behavior (lookup-then-insert-then-refetch-on-IntegrityError) but it's untested
for this multi-cluster path.

## #10 — End-to-end test asserts provenance only for Action Items
STILL OPEN. `tests/test_end_to_end.py::test_upload_to_published_record`
asserts `body["action_items"][0]["provenance"] == "inferred"` (only assertion
of its kind) plus `citations` on action_items; for `key_topics` it only checks
`title`, for `decision_points` only `settled`, and `minutes` isn't asserted on
at all. As described, it would still pass if `_cited`'s filtering were deleted
for topics/minutes/decisions since nothing checks their citations/provenance.

## #11 — No token budget when building extraction prompts
STILL OPEN. `_ask()` in `src/meeting_mgr/pipeline/extract.py:84-86` still
concatenates the full `render_cited_transcript(meeting_id)` into every one of
the four `_HEADER + instruction + transcript` prompts with no length check,
truncation, or chunking. No map-reduce/windowing exists anywhere in the file.

## #12 — Diarizer service buffers the whole upload in memory
STILL OPEN. `services/diarizer/main.py:26` still does
`tmp.write(await file.read())` — reads the entire upload into memory before
writing, exactly the pattern the issue flags. The suggested chunked-read fix
(`while chunk := await file.read(1 << 20): ...`) is not present.

## #13 — render_transcript has no secondary sort for simultaneous speech
STILL OPEN. `src/meeting_mgr/pipeline/attribute.py:31` (`render_transcript`)
orders by `.order_by(Segment.start_seconds)` only — no `Segment.id` tiebreak.
Same pattern also present in `extract.py:82`
(`render_cited_transcript`, also `order_by(Segment.start_seconds)` only),
which the issue doesn't name but shares the bug.

## #14 — failed_stage records only the last failing optional stage
STILL OPEN. `Meeting.failed_stage` (`src/meeting_mgr/models/meeting.py:21`) is
still `Mapped[str | None]`, single column. `set_stage_failure()`
(`src/meeting_mgr/pipeline/app.py:52-55`) does
`m.status, m.failed_stage = "failed", stage` — a straight overwrite. In
`orchestrate.py`, `OPTIONAL_STAGES` failures `continue` the loop rather than
aborting, so if `key_topics` and `minutes` both fail, only `minutes` survives
in `failed_stage`. No schema change (e.g. JSON list) present.

## #15 — Unknown from_stage raises a bare ValueError on the resume path
STILL OPEN. `src/meeting_mgr/pipeline/orchestrate.py:47-48`:
`start = names.index(from_stage) if from_stage else 0` — still calls
`.index()` directly with no membership check or friendly error message. An
unknown stage still raises the bare `ValueError: 'typo' is not in list`.

## #16 — Extraction creates Participant rows from unvalidated model output
STILL OPEN (deliberately deferred, `ready-for-human`). `resolve_participant`
(`src/meeting_mgr/participants.py:15-25`) still creates a `Participant` on
first sight of any non-blank name, called from `extract_action_items`,
`extract_decision_points` (`extract.py`) and `attribute()` (`attribute.py`).
Blank/whitespace names are guarded (`if not name or not name.strip(): return
None`), matching the issue's own note that "blank names are guarded... marks
everything inferred." No ADR in `docs/adr/` documents this decision (checked
0001-0003); the only record of the deliberate-acceptance rationale is the
issue body itself. Code behavior matches what the issue describes as still
outstanding — not resolved, correctly labeled ready-for-human.

## #17 — Orphaned object window between storage write and database commit
STILL OPEN (deliberately deferred, `ready-for-human`). `create_meeting()` in
`src/meeting_mgr/api/meetings.py:60-73`: `put_stream(key, file.file)` (line
70) is still called inside the `with get_org_session(...)` block, before the
session/transaction commits at context-manager exit. No reconciliation job or
`try/except` compensating delete around the storage write. No ADR addresses
this (docs/adr/0003 is about provenance, not storage/retention). Matches
issue description exactly; correctly labeled ready-for-human, not resolved.

## #18 — Settings defaults are the test values
STILL OPEN. `src/meeting_mgr/config.py` still defaults `database_url` to
`postgresql+psycopg://postgres:test@localhost:55432/meeting_mgr_test`,
`redis_url` to `redis://localhost:56379/0`, `s3_endpoint` to
`http://localhost:59000`, `asr_base_url`/`llm_base_url` to
`http://localhost:58080/v1`, `diarizer_url` to `http://localhost:58081` — all
test-shaped ports, none made required (no `str` without default). Contrast:
`session_secret` and `oidc_*` DO have comments marking them "deliberately
invalid placeholders... MUST override" — that pattern was applied to
auth-related settings but not to the connection settings the issue is about.
`docker-compose.yml` confirms it always overrides these explicitly (lines
27-37), so only a bare/self-hosted deployment is exposed, as the issue notes.
Suggested fix (make required, no default) not applied.

---

## Summary table

| # | Verdict | Evidence |
|---|---------|----------|
| 8 | STILL OPEN | `tests/test_align.py` — no multi-span/empty-spans test |
| 9 | STILL OPEN | `tests/test_attribute.py` — no dedup-across-clusters test |
| 10 | STILL OPEN | `tests/test_end_to_end.py::test_upload_to_published_record` — provenance only asserted on action_items |
| 11 | STILL OPEN | `src/meeting_mgr/pipeline/extract.py:84-86` `_ask()` — no length guard |
| 12 | STILL OPEN | `services/diarizer/main.py:26` `tmp.write(await file.read())` |
| 13 | STILL OPEN | `src/meeting_mgr/pipeline/attribute.py:31` — `order_by(Segment.start_seconds)` only |
| 14 | STILL OPEN | `src/meeting_mgr/models/meeting.py:21` single `failed_stage` column; `pipeline/app.py:52-55` overwrites |
| 15 | STILL OPEN | `src/meeting_mgr/pipeline/orchestrate.py:47-48` bare `names.index(from_stage)` |
| 16 | STILL OPEN (deliberate, ready-for-human) | `src/meeting_mgr/participants.py:15-25` creates Participant on any non-blank name; no ADR, only issue text documents deferral |
| 17 | STILL OPEN (deliberate, ready-for-human) | `src/meeting_mgr/api/meetings.py:70` `put_stream` before session commit; no ADR |
| 18 | STILL OPEN | `src/meeting_mgr/config.py` — db/redis/s3/asr/llm/diarizer URLs still default to test ports, not required |
