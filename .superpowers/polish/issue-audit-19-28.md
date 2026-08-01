# Issue audit: #19-#28 (excluding #22)

## #19 — Default credentials and exposed ports in docker-compose.yml
**PARTIALLY RESOLVED (documentation-only fix).** `docker-compose.yml` still ships
`postgres`/`meeting` and MinIO `meeting`/`meetingmeeting` with ports 8000/9000/9001
(now also 5173/8081) published — no code change. But `docs/admin-guide.md:64-65`
documents every exposed port, and `docs/admin-guide.md:156-181` has an explicit
"defaults below are test/development values... not suitable for production" table
covering DB password, MinIO creds, and `SESSION_SECRET`. The issue asked for "a
deployment note at minimum" — that note now exists, so the accepted (docs) form of
resolution is in place; the compose file itself is unchanged.

## #20 — ensure_bucket issues a head_bucket round-trip on every upload
**STILL OPEN.** `src/meeting_mgr/storage.py:22-32` `ensure_bucket()` still does a
`head_bucket` (and possibly `create_bucket`) on every call, no caching/memoization.
Still called per-request from `api/meetings.py:59`, `api/capture.py:72`,
`api/bot.py:135`, and `pipeline/watch.py:122`.

## #21 — storage._client builds a new boto3 client on every call
**STILL OPEN.** `src/meeting_mgr/storage.py:9-16` `_client()` still constructs a
fresh `boto3.client(...)` on every invocation; no `@lru_cache`/singleton. Every
storage function (`put_object`, `put_stream`, `get_object`, `get_stream`,
`append_stream`, `delete_object`, `list_keys`, `open_object`) calls `_client()`
independently — the surface has grown since Phase 1, making this worse, not better.

## #23 — No src/meeting_mgr/__init__.py (PEP 420 namespace package)
**STILL OPEN.** `src/meeting_mgr/__init__.py` still does not exist; `import
meeting_mgr; meeting_mgr.__file__` is `None`, confirming it's a namespace package.
`pyproject.toml`'s `[tool.hatch.build.targets.wheel] packages = ["src/meeting_mgr"]`
and `[tool.pytest.ini_options] pythonpath = ["src"]` both work fine with this, so it
is not breaking anything — but it is not "no longer applicable" either: every
subpackage underneath it (`api/`, `pipeline/`, `models/`, `auth/`, `inference/`) DOES
have a regular `__init__.py`, so the top-level package is now the sole
inconsistent exception rather than a project-wide convention.

## #24 — db.py creates the engine at import time
**STILL OPEN, and the surface has grown.** `src/meeting_mgr/db.py:13`
(`engine = create_engine(...)`) still executes at import time, and a second one was
added: `src/meeting_mgr/db.py:56` (`org_engine = create_engine(...)`) for the
RLS-scoped role. Both still module-level. Four session factories now depend on
these two engines (`get_session`, `get_readonly_session`, `get_org_session`,
`get_readonly_org_session`), so any future app-factory refactor now has two engines
to defer instead of one.

## #25 — asr.py catches ValueError where llm.py lists JSONDecodeError explicitly
**STILL OPEN.** `src/meeting_mgr/inference/asr.py:45` still catches
`(httpx.HTTPError, ValueError, ValidationError)`; `src/meeting_mgr/inference/llm.py:39-46`
still lists `json.JSONDecodeError` explicitly alongside `httpx.HTTPError, KeyError,
IndexError, TypeError, ValidationError`. Asymmetry unchanged; both are still
functionally correct since `JSONDecodeError` subclasses `ValueError`.

## #26 — NormalizeError carries raw ffmpeg stderr including temp paths
**STILL OPEN (though currently not client-visible in practice).**
`src/meeting_mgr/pipeline/normalize.py:62` (`raise NormalizeError(proc.stderr.decode()[-800:])`)
and `:78` still embed raw, unsanitized ffmpeg/ffprobe stderr, which includes the
tempdir path (`src/meeting_mgr/pipeline/normalize.py:47-49` builds `src`/`dst` under
`tempfile.TemporaryDirectory()`). Traced the call chain: `pipeline/orchestrate.py:29`
runs `normalize` as a pipeline stage; on exception, `orchestrate.py:53-58` catches
`Exception` generically and calls `set_stage_failure(meeting_id, "normalize")`
(`pipeline/app.py:52-54`), which stores only `status` and `failed_stage` — the
exception's string message is never persisted to the DB or returned by any API
endpoint today. So the leak is currently inert (dev/ops logs only), but the code
itself does not sanitize at the point the issue asked for.

## #27 — hasattr guard in test_normalize only catches bare-name imports
**STILL OPEN, matches issue's own assessment.** `tests/test_normalize.py:38-39`
still uses `assert not hasattr(mod, "get_object")` / `"put_object"`, which the issue
itself already characterizes as "a known limit rather than a hole" since the
companion spy assertions on `get_stream`/`put_stream` (lines 43-65) are the real
discriminator. No change needed per the issue's own text; nothing has changed in the
test.

## #28 — PEP 8: single blank line between top-level classes in models
**RESOLVED.** `ruff format --check src/meeting_mgr/models/` reports "9 files already
formatted" — all model files now have two blank lines between top-level classes
(e.g. `src/meeting_mgr/models/meeting.py:13-15`, `:26-28`, `:39-41`, `:47-49`).
CI enforces this: `.github/workflows/quality.yml:227-228` runs
`uv run ruff format --check .`. The issue's own prediction ("would be caught
automatically by a linter in CI") is exactly what happened.
