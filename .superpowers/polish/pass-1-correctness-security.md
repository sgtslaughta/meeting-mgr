# Polish Pass 1 — Correctness & Security

Scope: tenancy leaks, authz gaps, vacuous/mis-attributing tests, data-loss paths,
auth weaknesses, concurrency. Method: read-only review of `src/meeting_mgr/**`,
`migrations/**`, and `tests/**`; no files edited, no worktree needed (nothing was
run — everything below is from static reading unless marked VERIFIED).

Overall impression: this codebase is unusually disciplined for a six-phase
project. `authz.py` really is the sole chokepoint, `get_session()`/
`get_readonly_session()` are used only at the four documented bootstrap sites
plus pipeline/Celery, purge sibling-survival is explicitly tested per table,
and both enumeration oracles I checked (login, bot token) are closed by
construction with a matching test that pins the mechanism, not just the
outcome. I found no Critical issues. Two Important findings (one reasoned,
not verified) and one Minor.

## Findings

### 1. [Important, reasoned not verified] TOCTOU between chunk upload and finish() can silently drop the last chunk(s)

Files:
- `src/meeting_mgr/api/capture.py:65-84` (`upload_chunk`) and `:97-128` (`finish_capture`)
- `src/meeting_mgr/api/bot.py:120-136` (`upload_chunk`) and `:159-209` (`finish_session`)

What breaks: `upload_chunk` checks `m.status == "capturing"` and then writes
the chunk to object storage; `finish_capture`/`finish_session` independently
checks the same status, lists whatever keys currently exist in storage, and
builds the manifest from that snapshot. Neither the status check nor the
`list_keys()` read takes any lock on the Meeting/BotSession row, and in
`capture.py` the S3 write (`put_stream`, line 83) happens *after* the
`get_org_session` block has already committed and closed (line 65-69 exits
before line 83 runs) — widening the race window further than in `bot.py`,
where the write is still inside the transaction.

Concrete scenario: client uploads chunk N, request is slow (S3 latency or
network), client times out and retries by calling `/capture/finish` (or a bot
supervisor issues `finish` immediately after firing off the last chunk PUT
without awaiting it). `finish_capture`'s `list_keys(prefix)` runs before
chunk N's `put_stream` completes, so chunk N is absent from the manifest.
`finish_capture` still succeeds (multiple chunks 0..N-1 exist), status flips
to `pending`, pipeline runs on a manifest missing the tail of the recording.
The orphaned chunk N object is written afterward but never referenced by any
manifest — it also then leaks forever (nothing purges chunks outside a
manifest).

Why not "Critical": the code already documents that a *gap* in the sequence
is tolerated as a "lossy capture" by design (capture.py:116-122), so this is
an extension of an accepted risk, not a break of an explicit invariant — but
losing the *last* chunk specifically (not an arbitrary gap) is more likely in
practice than a random earlier one, and the docstring's stated rationale
("cannot distinguish chunk 3 dropped from chunk 3 never recorded") doesn't
apply to this failure mode, where the chunk genuinely exists but arrives a
moment too late.

I did not attempt to reproduce this with real concurrent requests (would
require racing two live HTTP calls against a running server/S3-compatible
store); flagging as reasoned-from-code, not verified.

### 2. [Minor] Two hand-rolled `organization_id !=` comparisons duplicate what RLS already enforces

Files:
- `src/meeting_mgr/api/bot_credentials.py:46-48`
- `src/meeting_mgr/api/watch_folders.py:61-63`

Both do:
```python
owner = s.get(Account, body.owner_account_id)
if owner is None or owner.organization_id != account.organization_id:
    raise HTTPException(422, ...)
```
inside a `get_org_session(account.organization_id)` block. `account` has
`tenant_isolation` RLS (migration `b3f2a1c9d4e7`), so `s.get(Account, other_org_id)`
already returns `None` under `get_org_session` for any cross-org id — the
`owner.organization_id != account.organization_id` half of the condition is
dead code today (VERIFIED by reading the migration; not exercised at
runtime). This is exactly the "second hand-rolled org comparison" pattern
`authz.py`'s module docstring calls out as a defect-in-waiting elsewhere in
the codebase, just for Account ownership rather than Meeting visibility — a
second place the check could in principle disagree with RLS (e.g. if a
future refactor swapped this session for `get_session()`, or if `meeting_app`
were ever granted BYPASSRLS), and there is no test that isolates the "RLS
alone" case from the "app check alone" case (the only cross-org test,
`tests/test_api_bot_credentials.py`'s `test_...422`, would pass identically
if the explicit comparison were deleted — mis-attribution risk, not a
present vulnerability).

Not exploitable today; recommend either deleting the explicit check (and
adding a comment that RLS is what makes `owner is None` cover the cross-org
case) or, if kept as defense-in-depth, adding a test that forces the RLS
layer to see the row (e.g. temporarily querying via `get_session()` in the
test) so the app-layer check has an actual pinning test rather than one that
merely agrees with RLS.

### 3. Everything else checked came back clean

Verified by reading (not exploited/run), listed so the other two concurrent
passes don't re-walk the same ground:

- `authz.py` chokepoint: every Meeting-touching endpoint in `api/meetings.py`,
  `api/edits.py`, `api/capture.py` calls `authorize()`; every non-Meeting
  admin action calls `require_role()`. `api/bot.py` is the only exception,
  matches the documented sanction exactly.
- `get_session()`/`get_readonly_session()` usage is confined to the four
  documented bootstrap sites (`api/auth.py` login + oidc_callback,
  `auth/deps.py` get_current_account, `auth/bot_deps.py` get_bot_credential)
  plus pipeline/Celery modules (`normalize.py`, `purge.py`, `pipeline/bot.py`,
  `pipeline/watch.py`). No stray usage in any request-handling endpoint.
- Login (`api/auth.py`) and bot-token auth (`auth/bot_deps.py`) both close the
  enumeration oracle the same way: a fixed dummy hash, `verify_password` run
  on every path, and a boolean flag (not the hash-compare result alone)
  gating success. `tests/test_bot_auth_deps.py::test_verify_password_runs_on_every_rejection_path`
  and `test_every_rejection_path_is_indistinguishable` actually pin the
  mechanism (spies on the call, and diffs full `(status, detail)` tuples
  across every rejection shape) rather than just asserting a status code.
- `auth/mtls.py` allowlist check uses `scope.get("client")` (network-layer
  source IP, not a header) and defaults to an empty allowlist
  (`config.py:31`, `mtls_proxy_allowlist_raw = ""`) — fails closed by default.
- Purge (`pipeline/purge.py`, `retention.py`): manifest-then-chunks delete
  ordering is correct and crash-recoverable (manifest deleted last); cascade
  delete via `ON DELETE CASCADE` is correctly noted to bypass RLS (matches
  Postgres docs) and is asserted directly in
  `test_purge_meeting_full_removes_the_meeting_and_every_child_row`. Sibling
  survival within one org is explicitly tested in `test_purge_full.py`,
  `test_purge_audio.py`, and `test_purge_organization.py` (not just cross-org
  isolation) — this is exactly the blind spot the task brief warned about,
  and it's covered.
- `bot.py::start_session`'s check-then-insert race is closed correctly with
  `s.begin_nested()` around the INSERT and a re-query on `IntegrityError`
  (the SAVEPOINT the task brief asked me to check for missing instances of).
  Same pattern in `participants.py::resolve_participant`.
  `watch_folder.py::upsert_watch_folder` deliberately does NOT have this
  protection and says so in a comment (admin-only path, low risk) — a
  documented, not accidental, gap.
- `bot.py::_owned_session`'s two-column filter
  (`bot_credential_id=credential.id, organization_id=credential.organization_id`)
  has a genuine kill-test isolating the `bot_credential_id` half
  (`tests/test_api_bot_sessions_chunks.py::test_a_session_belonging_to_another_credential_in_the_same_org_is_not_found_for_chunk_upload`,
  same-org-different-credential setup) — this is the good version of the
  pattern finding #2 above lacks.
- `sweep_stale_bot_sessions` (`pipeline/bot.py`) re-checks
  `m.status == "capturing"` inside its own `get_org_session` write, closing
  the race against a concurrent `finish_session` correctly (unlike finding
  #1, this one re-validates right before the write, in the same transaction
  that does the write).
- `readable_meetings_filter()` / `_can_read()` agreement is enforced by
  `test_readable_meetings_filter_agrees_with_can_read` in `test_authz.py`,
  and the visibility test suite (`test_authz.py`) covers owner/shared/org/
  auditor/admin axes with tests that each isolate one axis (e.g. cross-org
  share denial, admin-of-one-org-cannot-read-another).
- Chunk ordering (`capture.py::_chunk_seq`, `bot.py::_bot_chunk_seq`) sorts on
  the parsed integer, not the zero-padded string, and
  `tests/test_api_bot_sessions_chunks.py::test_listing_sorts_numerically_not_lexically_past_ten`
  actually creates >10 chunks so zero-padding alone couldn't make the test
  pass by coincidence — this is the "numeric sort vs zero-padding" trap the
  brief called out, and it's handled correctly.

## Not investigated / lower confidence

Given the size of the codebase (3822 LOC in `src/meeting_mgr`) and the
analysis-only constraint, I prioritized the tenancy/authz/purge/auth surface
explicitly called out in the brief and did not deeply review:
`pipeline/align.py`, `pipeline/diarize.py`, `pipeline/attribute.py`,
`pipeline/transcribe.py`, `pipeline/extract.py`, or the frontend. These are
lower-risk from a tenancy/authz standpoint (pure processing, not
request-handling) but were not read line-by-line.
