# Phase 1 — Deferred Findings

Findings raised during Phase 1's task reviews and consciously deferred rather
than fixed. All were judged non-blocking by the final whole-branch review.

Three findings originally on this list were promoted and fixed before merge:
non-deterministic alignment tie-break, the missing `Participant` uniqueness
constraint, and an unscoped Segment query in the end-to-end test. They are not
repeated here.

Read this before planning Phase 2 or 3 — several items are cheaper to fix
before the code they touch grows.

---

## Test infrastructure

- **No per-test database cleanup.** Tests share a persistent Postgres. Four
  test files hand-scope queries by `meeting_id` to compensate, and two tests
  were found passing for the wrong reason before being scoped. A conftest
  truncate-or-rollback fixture would remove a whole class of order-dependent
  flakes. Until then the suite cannot run under `pytest-xdist`.
- `make_meeting` leaves Meeting and Recording rows behind; no reset strategy.
- The fake inference server's `push_raw` unconditionally takes precedence over
  `push_chat`. A future test queuing both would be served entirely from `raw`
  until it drained.
- Coverage gaps, all verified correct by reading but unpinned by a test:
  `push_error`/`push_transcription`/`.requests` on the fake server; cascade
  deletes and the tenancy boundary in `test_models.py`; missing-key `get` and
  `delete` idempotency in storage; zero-segment transcription; exact-tie,
  multi-span and empty-`spans` alignment; the Participant-dedup path.
- The end-to-end test asserts `provenance` only for Action Items, and would
  still pass if citation filtering were deleted — it exercises the
  pass-through path only. The negative cases live in `test_extract.py`.

## Correctness and robustness

- **No token budget in `extract.py`.** `_ask` concatenates the full transcript
  into each of four LLM calls. Fine against stubbed inference; a real long
  meeting will truncate or error. Needs a chunking or map-reduce strategy —
  a design decision, not a patch.
- **`services/diarizer/main.py` buffers its incoming upload** via
  `await file.read()`. The client side was fixed to stream in three places;
  this is the server-side counterpart, in the one image we do not build or
  test in CI.
- `render_transcript` orders only by `start_seconds`; simultaneous speech has
  no deterministic tiebreak, so prompt text can vary between runs.
- Two failing optional extraction stages leave only the last in
  `Meeting.failed_stage`. Real information loss for a failure UI, and the fix
  is a schema change (`failed_stages` as a list) that is easier before data
  accumulates — do it in whichever phase builds that UI.
- An unknown `from_stage` raises a bare `ValueError` from `names.index` on the
  operator-facing resume path.
- Extraction creates `Participant` rows as a side effect of unvalidated model
  output. A hallucinated name becomes a permanent domain entity. Accepted
  behavior, shared with the attribution stage.
- Orphaned-object window on upload: if `put_stream` succeeds and the commit
  then fails, the object outlives its row. No reconciliation job exists.

## Deployment and configuration

- **`Settings` defaults are the test values** (`localhost:55432`, etc.). A
  production deployment that forgets `DATABASE_URL` connects to a nonexistent
  test-shaped endpoint instead of failing loudly at startup. Consider making
  them required before anyone self-hosts unsupervised.
- Default credentials and exposed ports in `docker-compose.yml` — acceptable
  for a self-hosted dev stack, not for anything internet-facing.
- `ensure_bucket()` issues a `head_bucket` round-trip on every upload; it only
  needs to succeed once.
- `_client()` constructs a new boto3 client per call, with no reuse.
- **No CI pipeline exists** (`.github/workflows` or equivalent). The final
  review recommended adding one as a fast-follow, now that the whole-branch
  review has been done once by hand.

## Cosmetic

- No `src/meeting_mgr/__init__.py` — relies on PEP 420 namespace packages.
- `db.py` creates the engine at import time; an app-factory wiring would want
  it lazy.
- `asr.py` catches `ValueError` where `llm.py` lists `json.JSONDecodeError`
  explicitly — equivalent, asymmetric.
- `NormalizeError` carries raw ffmpeg stderr including temp paths. Sanitize if
  it is ever surfaced to an API caller.
- The `hasattr` guard in `test_normalize.py` catches only bare-name imports; a
  module-qualified `storage.get_object()` call would evade it.
- Single blank line between top-level classes in `models/*.py` (PEP 8 wants two).

---

## Process note for future phases

`scripts/task-brief` writes a **snapshot**. Amending the plan does not update
already-generated briefs — regenerate them before any re-review, or the
reviewer judges new code against an old spec. This cost one full fix round in
Phase 1.
