# Polish Fix Report — Code

Scope: `src/` and `tests/` only, on `main`. Four items from pass 1 / pass 2, applied in
sequence, plus a coordinator-flagged regression fix on item 1.

## Status: complete, all four items applied, regression addressed.

## Commits

1. `fbea7d4` — fix: close TOCTOU window between capture/bot chunk finish and status flip
2. `980bc18` — refactor: collapse duplicated chunk-key helpers into chunk_storage.py (#38)
3. `fbbe7e2` — refactor: remove dead get_bot_credential_by_id helper
4. `90c7d2d` — test: isolate the RLS-vs-comparison mechanism behind the cross-org owner 422
5. `16744aa` — fix: make the "finishing" TOCTOU state reclaimable, not a permanent stall

## Item 1 — TOCTOU (capture.py / bot.py)

Fix shape: in `finish_capture`/`finish_session`, flip `Meeting.status` to an
intermediate `"finishing"` value FIRST, committed in its own transaction,
before taking the `list_keys()` snapshot (previously the status write to
`"pending"` was the LAST thing the function did, after building the
manifest — so the status stayed `"capturing"`, visible to concurrent
`upload_chunk` requests, for the entire body of `finish()`).

**Ordering guarantee closed:** any `upload_chunk` whose own status recheck
*begins* after the flip commits now sees `"finishing"` and is rejected with
409 before it ever calls `put_stream()`.

**Residual window (NOT closed, stated honestly):** a straggler whose status
recheck already committed `"capturing"` a moment *before* the flip lands is
already past its check and free to complete its `put_stream()` (a plain S3
call, no DB transaction around it) at any point afterward, including after
the `list_keys()` snapshot. That chunk still lands in storage but is
silently excluded from the manifest. The fix narrows the vulnerable window
from "the entire body of finish()" down to "one already-in-flight
straggler's remaining S3-PUT latency" — it does not eliminate it.

Empty-manifest handling: capture.py's empty-chunk case now reverts status
to `"capturing"` (was previously never touched) so the client can retry;
bot.py's empty case already went to `"failed"`, unchanged in shape.

Tests added (both fail if the fix is reverted):
- `tests/test_api_capture.py::test_finish_toctou_a_straggler_upload_after_the_status_flip_is_rejected_not_orphaned`
  — kill line: `m.status = "finishing"` moved back to the end of the
  function (its pre-fix position) turns this red.
- `tests/test_api_bot_sessions_chunks.py::test_finish_toctou_a_straggler_upload_after_the_status_flip_is_rejected_not_orphaned`
  — same kill line, `finish_session`.
- `tests/test_api_capture.py::test_finish_reverts_to_capturing_when_no_chunks_survive_the_flip`
  — kill line: dropping `m.status = "capturing"` on the empty path.

Both race tests use a monkeypatched no-op hook (`_finish_race_hook`) called
between the flip-commit and the snapshot; the test replaces it with a real
authenticated chunk PUT, simulating the straggler interleaving without real
concurrency.

## Item 5 (regression fix on item 1) — "finishing" was a permanent stall

The coordinator caught this after item 1 landed: committing
`Meeting.status = "finishing"` as the FIRST step, with all the
manifest-building work happening afterward in a separate, later
transaction, meant a crash in that gap stranded the Meeting permanently.
`sweep_stale_bot_sessions` only ever selected `status == "capturing"`, and
a retried `finish()` 409'd on any non-"capturing" status forever — so
nothing could move a `"finishing"` Meeting anywhere. Before item 1, the
same crash left `status == "capturing"`, which the sweep did eventually
fail out. Item 1 traded a narrow silent-truncation window for a strictly
worse, permanent-stall failure mode in the crash case. Confirmed and fixed.

**bot.py** keeps `finish_session`'s 409-on-retry behavior (deliberately NOT
retryable — a genuinely in-flight `finish()` must not be raced by a naive
retry attempting to resume/duplicate its work). Recovery is entirely via
`sweep_stale_bot_sessions` (`pipeline/bot.py`), extended to also select
Meetings with `status == "finishing"`, reusing the existing
`BotSession.last_activity_at` heartbeat and cutoff — `finish_session` never
touches `last_activity_at`, so by the time a Meeting has sat in
`"finishing"` past the staleness threshold, that timestamp is already
stale from the last real chunk upload before `finish()` was even called;
no new column was needed. Swept `"finishing"` Meetings are **failed out**,
not resumed — a half-built manifest is not obviously safe to
auto-complete from a background sweep with no request context — with a
distinct `failed_stage` (`"bot_ingest_finish_stuck"`, vs `"bot_ingest"` for
the pre-existing stale-mid-capture case) so an operator can tell "crashed
while finishing, chunks may be orphaned in storage" apart from "never made
it past its first chunk." The sweep's status filter is an explicit
`Meeting.status.in_(("capturing", "finishing"))` tuple, and I confirmed
(read of every `.status` write site in `src/`) no other code path ever
sets a Meeting to `"finishing"` for a different reason — this branch
cannot accidentally match an unrelated status. `Meeting.status` remains a
plain `String(20)` with no enum/CHECK constraint, unchanged from item 1.

**capture.py**: verified there is no sweep, or any other reclaim
mechanism, for browser-capture Meetings at all — not for `"capturing"`,
and (before this fix) not for `"finishing"` either. This is a pre-existing
gap, not one item 1 introduced: a browser-capture Meeting that crashes
mid-capture (before `finish()` is ever called) was already stuck in
`"capturing"` forever, with nothing to reclaim it, on `main` before any of
this pass's changes. Building a `sweep_stale_captures` task (a
BotSession-equivalent heartbeat doesn't exist for browser capture — it
would need a new `Meeting.updated_at`-style column and a new Celery beat
entry, mirroring `pipeline/bot.py`'s shape) is a reasonably-sized follow-up
but out of scope to build unprompted here; **proposed location**:
`pipeline/capture.py` (new file, mirroring `pipeline/bot.py`), a new
`Meeting.updated_at` column bumped by `upload_chunk`/`finish_capture`, and
a `sweep-stale-captures` beat entry.

Given no sweep exists to close the `"finishing"` gap for capture, I made
`finish_capture` retryable instead: it now accepts `"finishing"` as well
as `"capturing"` on entry, so a manually retried `finish()` call (by a
client, or an operator) is the recovery path. Making this safe required
one more change: the transition OUT of `"finishing"` (to `"pending"` or
back to `"capturing"`) is now a conditional `UPDATE ... WHERE status =
'finishing'` (`Query.update()`, checked by rowcount) instead of a
read-then-write `m.status = ...` — because once `"finishing"` is a valid
retry entry point, two concurrent `finish()` calls on the same Meeting (a
retry racing a still-genuinely-in-flight original call, not just a
crash-recovery retry) could otherwise both pass the entry check and both
build a manifest, inserting two `Recording` rows. The `UPDATE...WHERE` is
atomic at the row level: exactly one caller's statement matches and
updates per transition; the loser detects zero rows affected and reports
whatever is now true instead of writing a duplicate.

**Crash-point walk (the invariant: no crash point leaves a Meeting in a
state nothing can move it out of), stated in code comments at both finish
functions:**
- Before the status-flip transaction commits: status is untouched
  (`"capturing"`) — a retry/re-entry starts over from scratch. Always
  recoverable.
- After the flip commits, before the second transaction: status is
  `"finishing"`. bot.py → recovered by the sweep (fails out). capture.py →
  recovered by a retried `finish()` call (resumes).
- During the second transaction (manifest build + CAS transition): it
  either commits as a whole (status moves on) or rolls back as a whole
  (status stays `"finishing"`, unchanged) — Postgres transaction atomicity
  means no partial Recording-without-status-flip (or vice versa) state is
  possible.
- After the second transaction commits, before `run_pipeline()`'s Celery
  dispatch: status is `"pending"` but the pipeline was never enqueued.
  **This is a separate, pre-existing gap** (not introduced or widened by
  either the original TOCTOU fix or this regression fix) — nothing sweeps
  a Meeting stuck in `"pending"` with no pipeline run. Flagged here, not
  fixed: out of scope for this pass, and worth its own follow-up (a sweep
  for stale `"pending"` Meetings, or making `run_pipeline()` dispatch
  transactionally with the status write via a Celery-Postgres outbox
  pattern).

Tests added (constructing the stranded state directly, not simulating a
crash, per instruction):
- `tests/test_bot_stale_sweep.py::test_a_meeting_stuck_in_finishing_is_reclaimed_and_failed_out`
  — kill line: removing `"finishing"` from the sweep's
  `Meeting.status.in_((...))` filter.
- `tests/test_bot_stale_sweep.py::test_a_fresh_finishing_meeting_is_left_alone`
  — proves the staleness cutoff (not just the status filter) gates the
  sweep; kill line: dropping the `BotSession.last_activity_at <= cutoff`
  condition.
- `tests/test_api_capture.py::test_a_meeting_stranded_in_finishing_is_reclaimed_by_a_retried_finish_call`
  — kill line: reverting the entry check back to `if m.status !=
  "capturing"`.
- `tests/test_api_capture.py::test_a_second_concurrent_finish_call_does_not_duplicate_the_recording`
  — uses the item-1 race-hook seam to make a nested `finish()` call land
  between the outer call's flip-commit and its own transition; kill line:
  replacing the `UPDATE...WHERE status == "finishing"` with a plain
  `m.status = "pending"` read-then-write.
- `tests/test_api_capture.py::test_a_retried_finish_with_no_chunks_still_reverts_to_capturing`
  — the empty-manifest retry path lands on the same recoverable shape.

## Item 2 — chunk-key helper consolidation (#38)

New `src/meeting_mgr/chunk_storage.py`: `chunk_prefix(meeting_id, subdir)`,
`chunk_key(meeting_id, seq, subdir, suffix)`, `chunk_seq(prefix, suffix,
key)`. `capture.py`/`bot.py` keep thin same-named wrappers
(`_chunk_key`/`_bot_chunk_key` etc.) bound to their own subdir/suffix, so
call sites are unchanged. Both the `{seq:06d}` zero-padding and the
numeric-sort key are preserved unchanged; only subdir/suffix became
parameters. `pipeline/watch.py` does not participate (confirmed, no
chunk/manifest concept there).

Added `tests/test_api_capture.py::test_capture_chunks_reassemble_byte_for_byte_in_upload_order`
(capture-path equivalent of the existing bot-path byte-compare test) so the
shared helper is exercised from both call sites, not just bot's.

## Item 3 — dead `get_bot_credential_by_id`

Re-grepped before deleting: only callers were `tests/test_bot_credential_helpers.py`.
`auth/bot_deps.py` inlines `s.get(BotCredential, ...)` directly. Removed the
function and its two dedicated tests
(`test_get_by_id_returns_none_when_absent`,
`test_get_by_id_returns_the_matching_row`); the one remaining test that used
it purely as a verification helper (`test_revoke_requires_credential_id_and_org_id_to_agree`)
now reads via `s.get(BotCredential, ...)` directly.

## Item 4 — dead cross-org owner comparison

Not deleted, per instructions. Added a comment at each site
(`api/bot_credentials.py:47`, `api/watch_folders.py:62`) recording that
RLS is the active guard, the comparison is currently unreachable
defence-in-depth, and it's kept for if the endpoint ever moves off
`get_org_session`.

Two isolating tests per file:
- `test_cross_org_owner_lookup_returns_none_under_rls_is_the_active_guard`
  — proves `s.get(Account, other_org_id)` under `get_org_session` returns
  `None` by itself. Kill line: dropping `tenant_isolation` RLS on
  `account`.
- `test_owner_organization_mismatch_comparison_is_correct_if_ever_reached`
  — uses an RLS-exempt `get_session()` to show the comparison is itself
  correct (`owner is not None` and `owner.organization_id != org_a`), i.e.
  it would decide the outcome alone if RLS were ever bypassed. Kill line:
  inverting `!=` to `==`.

## Test summary

`uv run pytest -q` green twice in a row against the shared persistent
Postgres: 415 passed both runs (coordinator-confirmed baseline 410 + 5 new
tests from the item-5 regression fix: 2 sweep tests, 3 capture tests).
`ruff check` and `ruff format --check` clean on `src/` and `tests/` (an
unrelated pre-existing `ruff format` diff exists in
`.superpowers/polish/pass-2-simplification.md`'s embedded code fence —
untouched, out of scope per the src/tests-only constraint).

## Concerns

- Item 1's TOCTOU fix is an honest narrowing, not a close — see the
  residual window described above and restated in the code comments at
  both finish functions. A fully closed fix would need either a real
  lock/lease on the Meeting row held across the S3 write in `upload_chunk`,
  or making `put_stream()` participate in the same transaction boundary as
  the status check — both larger changes than this pass's scope, and
  `upload_chunk`'s own docstring already documents that its S3 write is
  deliberately outside any DB transaction (never-buffer-in-memory
  constraint interacting with streaming uploads).
- `Meeting.status` is a plain `String(20)` with no enum/CHECK constraint.
  `"finishing"` is a new transient value; confirmed by reading every
  `.status` read/write site in `src/` that no other code path sets or
  branches on it for a different reason, and the sweep's filter is an
  explicit `.in_(("capturing", "finishing"))` tuple, not a pattern match,
  so it cannot accidentally catch an unrelated status. It appears briefly
  in `GET /meetings` listings during the window between flip and the
  second transaction — for bot.py that's normally sub-second; for
  capture.py it can persist until either the original request completes
  or an operator/client retries `finish()`.
- capture.py has no automated reclaim for `"finishing"` (or, pre-existing,
  for `"capturing"`) — recovery there is retry-only (manual or
  operator-driven), not swept. This is a known, stated gap, not silently
  left implicit; see the item 5 section above for the proposed location of
  a future `sweep-stale-captures` task.
- A separate, pre-existing crash point (status reaches `"pending"` but
  `run_pipeline()`'s Celery dispatch never happens) exists in both finish
  paths and predates this entire pass. Not fixed — flagged for follow-up.
- Item 4's tests exercise the mechanism via direct session calls, not the
  live HTTP endpoint, since the comparison is genuinely unreachable through
  the endpoint today — that's the point being proven, not a testing gap.
