# Polish Pass 2 — Simplification / Over-engineering audit

Scope: `src/meeting_mgr/**`, `web/src/**`. Analysis only, no edits made.
Overall impression: this codebase is unusually disciplined for its size —
almost every "duplication-looking" or "defensive-looking" line has a
docstring explaining a specific incident or invariant it exists to prevent
(RLS bypass windows, timing oracles, S3 append semantics, crash-recovery
ordering). Very little true fat. Findings below are correspondingly small.

---

## 1. Dead code — `get_bot_credential_by_id` (HIGH CONFIDENCE, but low LOC)

**File:** `src/meeting_mgr/bot_credentials.py:37-41`

```python
def get_bot_credential_by_id(s, credential_id: int) -> BotCredential | None:
    """Unscoped lookup by primary key. ..."""
    return s.get(BotCredential, credential_id)
```

**Grep confirms:** the only callers are `tests/test_bot_credential_helpers.py`
(lines 6, 56, 67, 196). No production code calls it —
`auth/bot_deps.py:get_bot_credential` does `s.get(BotCredential, int(credential_id_s))`
directly rather than going through this helper, and `api/bot_credentials.py`
never needs an unscoped lookup by id.

**Cut:** the function (5 lines) and its four direct-unit-test call sites in
`test_bot_credential_helpers.py` (would need reworking or deleting those
specific test cases — they test a helper nothing uses).

**What replaces it:** nothing; it's a one-line wrapper around `s.get()`.

**Lines saved:** ~5 in source, plus whatever fraction of the 197-line test
file is dedicated solely to this function (looks like ~20-30 lines across
3 test functions).

**Risk:** Low. It's a pure read helper with no side effects and nothing else
depends on its unscoped-lookup contract. Confirm before deleting that no
planned future admin endpoint needs unscoped-by-id lookup (there's a
docstring implying it might be useful for "the token-resolution auth
dependency" — but that dependency doesn't actually call it, it inlines
`s.get`). If the intent was for `get_bot_credential` to use this helper and
that migration never happened, deleting the helper is correct; if it was
meant to unify, the fix is the opposite (make `bot_deps.py` call it) — flag
for the developer to decide, don't delete blind.

---

## 2. Duplicated chunk-key helpers — capture.py vs bot.py (issue #38, confirmed scope)

**Files:** `src/meeting_mgr/api/capture.py:16-25` and `src/meeting_mgr/api/bot.py:88-97`

```python
# capture.py
def _chunk_prefix(meeting_id): return f"raw/{meeting_id}/chunks/"
def _chunk_key(meeting_id, seq): return f"{_chunk_prefix(meeting_id)}{seq:06d}.webm"
def _chunk_seq(prefix, key): return int(key.removeprefix(prefix).removesuffix(".webm"))

# bot.py
def _bot_chunk_prefix(meeting_id): return f"raw/{meeting_id}/bot-chunks/"
def _bot_chunk_key(meeting_id, seq): return f"{_bot_chunk_prefix(meeting_id)}{seq:06d}.chunk"
def _bot_chunk_seq(prefix, key): return int(key.removeprefix(prefix).removesuffix(".chunk"))
```

Issue #38 already tracks this precisely and its description is accurate —
I did not find any duplication it missed. The two call sites that use these
helpers (`finish_capture`/`finish_session` manifest-building, and the two
`list_chunks` endpoints) are also near-identical in shape but operate on
different models (`Meeting`+`authorize()` vs `BotSession`+`_owned_session()`),
so consolidating beyond the key helpers themselves would cross into
`watch.py`'s territory unnecessarily — **watch.py does NOT participate in
this duplication** (it ingests one whole file per Meeting, no chunk/manifest
concept at all), so a 3-way "ingest path" abstraction is not warranted; only
capture.py/bot.py share the chunk shape.

**Cut:** replace both trios with one shared helper, e.g. in a new
`src/meeting_mgr/chunk_storage.py`:

```python
def chunk_prefix(meeting_id: int, subdir: str) -> str:
    return f"raw/{meeting_id}/{subdir}/"

def chunk_key(meeting_id: int, seq: int, subdir: str, suffix: str) -> str:
    return f"{chunk_prefix(meeting_id, subdir)}{seq:06d}{suffix}"

def chunk_seq(prefix: str, suffix: str, key: str) -> int:
    return int(key.removeprefix(prefix).removesuffix(suffix))
```

**Constraint respected:** both the `{seq:06d}` zero-pad (in `chunk_key`) and
the numeric-sort key (`chunk_seq` used as the sort key in both `finish_*`
callers) must be kept — the helper above keeps both; only the subdir/suffix
becomes a parameter, not the ordering mechanism.

**Lines saved:** ~18 lines of near-identical helper code collapsed to ~8
shared + 2 one-line call-site wrappers (`_chunk_key = partial(chunk_key,
subdir="chunks", suffix=".webm")` etc., or just call the shared function
directly with explicit subdir/suffix args at each of capture.py's and
bot.py's ~6 call sites). Net roughly 15-20 lines removed, plus removes the
drift risk issue #38 flags.

**Risk:** Low — this is exactly what issue #38 scopes, and the issue itself
already flags the byte-compare-not-count-assertion testing requirement.
Not a sweeping refactor; single new small module, mechanical substitution.

---

## 3. `extract.py` — four extraction functions share ~80% of their structure

**File:** `src/meeting_mgr/pipeline/extract.py:104-197`
(`extract_key_topics`, `extract_minutes`, `extract_action_items`,
`extract_decision_points`)

Each function: build a prompt via `_ask()`, fetch `_valid_segment_ids()`,
open a `get_session()`, loop over the LLM's items, filter citations via
`_cited()`, skip if no valid citations remain, construct and `s.add()` a
model instance with `provenance="inferred"`. `extract_action_items` and
`extract_decision_points` additionally resolve one or more
`participant_name`s via `resolve_participant`.

This is real structural duplication, but I'm **not proposing a shared loop
abstraction** — the four functions differ in prompt text, output schema,
and per-item side effects (0, 1, or N participant resolutions), and
collapsing them into one parameterized function would trade ~30-40 lines of
saved boilerplate for a config-driven abstraction with exactly 4 callers,
which is the "speculative abstraction" pattern this pass is supposed to
flag, not commit. Noting it for completeness but **not recommending the
cut** — leave as-is unless a 5th extractor is added, at which point the
abstraction pays for itself.

---

## 3 files over 500 lines

None. Largest source files: `pipeline/watch.py` (271), `pipeline/purge.py`
(240), `api/edits.py` (233), `api/meetings.py` (221), `api/bot.py` (209).
All comfortably under the 500-line limit; no file does two unrelated jobs
(each maps 1:1 to one ingest/pipeline/api concern already).

---

## Things checked and deliberately NOT flagged (confirmed load-bearing / justified)

- `bot_config.py` (2 constants) / `watch_config.py` (1 constant): tiny
  single-purpose files, but each carries an explicit docstring explaining
  why it's split out (avoids a Celery import cycle between `pipeline/app.py`
  and `pipeline/bot.py`/`pipeline/watch.py`). Not worth merging — the
  cycle-avoidance reasoning is real and documented at both ends.
- `_DUMMY_HASH` in `api/auth.py` and `auth/bot_deps.py` — two near-identical
  constant-time-oracle-defeating patterns. Per task instructions, this is
  intentional defense-in-depth for two independent authentication surfaces
  (human login vs bot token), not duplication to collapse.
- `edits.py`'s `_run_extraction` if/elif dispatch (vs a dict of function
  refs) — explicitly documented as deliberate: a dict of function objects
  would freeze references at import time and break the tests' (and any
  future caller's) monkeypatching of `meeting_mgr.api.edits.extract_*`.
  Correct as written.
- `storage.py`'s `get_stream` vs `append_stream` — look redundant (both
  stream an S3 object to a file handle) but differ in a load-bearing way:
  `get_stream`/`download_fileobj` always writes from offset 0, so a second
  call into the same handle overwrites rather than appends; `append_stream`
  exists specifically to respect the handle's current position for
  chunk-manifest reconstruction (`pipeline/normalize.py`). Not duplication.
- `retention.py` / `purge.py` — extensive docstrings, but the actual code
  (candidate selection, purge ordering) is not duplicated elsewhere and each
  guard (e.g. `full_ids` exclusion before `.limit()`) is load-bearing for
  correctness, not defensive slop.
- Watch-folder / bot-session "sweep" tasks (`scan_watch_folders`,
  `sweep_stale_bot_sessions`, `sweep_retention`) look structurally similar
  (untenanted read → per-row `get_org_session` write, isolate-and-log
  per-item failures) but each is ~15-30 lines and used by exactly one beat
  schedule entry; abstracting the "isolate and log a per-org/per-item sweep
  loop" pattern across three call sites is arguable but marginal — the
  isolation/logging text differs meaningfully per call site (what "stale"
  means, what gets marked failed), and Celery task registration by name
  makes a shared base awkward. Not recommending.
- Frontend (`web/src/**`, 726 lines total): no dead exports found; `api.ts`
  is a flat set of one-line fetch wrappers with no unused entries (grepped
  every export against `web/src` importers). No consolidation opportunity
  worth the churn at this size.

---

## Summary

| # | Finding | Lines removable | Confidence |
|---|---|---|---|
| 1 | Dead `get_bot_credential_by_id` helper (+ its dedicated tests) | ~25-35 | High (grep-confirmed no prod caller) |
| 2 | Chunk-key helper duplication, capture.py/bot.py (issue #38, scope confirmed correct) | ~15-20 | High |
| 3 | extract.py structural duplication across 4 functions | 0 (noted, not recommended — single-caller-per-shape, abstraction not yet justified) | N/A |

**Total estimated removable: ~40-55 lines.** This is a small, well-factored
codebase; pass 2 did not find a big single deletion. Rank order for a
fix pass: (1) issue #38's chunk-helper consolidation — already tracked and
scoped correctly, just needs implementing; (2) delete/resolve
`get_bot_credential_by_id`; (3) nothing else rises to "worth touching."
