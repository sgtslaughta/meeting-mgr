# CI: quality.yml + security.yml

## Status
Complete. Two workflow files created: `.github/workflows/quality.yml`,
`.github/workflows/security.yml`. `pyproject.toml` gained `ruff` as a dev
dependency plus `[tool.ruff]`/`[tool.ruff.lint]` config. 48 files were
reformatted once by `ruff format .` to establish a clean baseline (mechanical,
whitespace-only), plus two genuine lint fixes (see below).

## Verified by actually running

- `uv run pytest -q && uv run pytest -q` — 78 passed, both runs, against the
  same Postgres (via `docker compose -f docker-compose.test.yml`, schema via
  `uv run alembic upgrade head`). This is the exact double-run CI does.
- `uv run ruff check .` — clean.
- `uv run ruff format --check .` — clean (66 files already formatted).
- Frontend: `npx vitest run` (31 passed), `npx tsc --noEmit` (clean),
  `npm run build` (clean) — exact commands the frontend job runs.
- `npm audit --json` in `web/` — confirmed exactly one advisory
  (GHSA-qwww-vcr4-c8h2, via react-router-dom → react-router). Hand-tested the
  allowlist filter script (jq) both against real output (passes) and against
  an injected fake second advisory (correctly fails, exit 1).
- `uv audit --preview-features audit-command` — "Found no known
  vulnerabilities" against the locked deps (uv has a native audit command,
  used instead of pip-audit).
- `docker build` for both root `Dockerfile` and `web/Dockerfile` — both
  succeed locally.
- `docker run aquasec/trivy:latest image --severity HIGH,CRITICAL
  --ignore-unfixed --exit-code 1` against both built images — both exit 0
  (clean) today, confirming the step's flags/behavior.
- `docker run zricethezav/gitleaks:latest detect` over full repo history (59
  commits) — no leaks found.
- YAML parses cleanly (`python -c "import yaml; yaml.safe_load(...)"`) for
  both workflow files.
- `actionlint` (via `docker run rhysd/actionlint`) against both workflow
  files — zero findings.
- Verified every pinned action tag (`checkout@v4`, `setup-node@v4`,
  `setup-uv@v3`, `codeql-action@v3`, `docker/build-push-action@v6`,
  `docker/setup-buildx-action@v3`, `trivy-action@v0.28.0`,
  `gitleaks-action@v2`) actually exists via `gh api repos/.../tags`.

## Unproven (cannot execute GitHub Actions here)

- The workflows have never actually run on GitHub's runners. Local
  verification reproduces every command each job invokes, and the health
  checks / service ports mirror `docker-compose.test.yml` exactly, but
  GH Actions `services:` container networking, the MinIO health-check
  command (I used an HTTP probe on `/minio/health/live` since GH Actions
  service containers can't override the image's CMD), CodeQL's Autobuild for
  this repo, and the Docker layer-cache behavior in `docker/build-push-action`
  are unverified in the actual Actions environment.
- gitleaks-action and trivy-action's GitHub-Action wrapper behavior (vs. the
  raw Docker images I tested) is unverified — I validated the underlying
  Docker images produce the expected pass/fail, not the Action wrappers.

## ruff configuration

```toml
[tool.ruff]
target-version = "py312"
line-length = 100
extend-exclude = ["migrations/versions"]  # alembic-generated boilerplate

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = [
  "B008",  # FastAPI Depends()/Query() default-arg pattern, not a bug
  "E702",  # deliberate local style: `s.add(m); s.flush()`, used 37x in tests
]
```

Genuine fixes applied (not blanket-ignored):
- `src/meeting_mgr/api/meetings.py`: `raise HTTPException(...) from None` in
  the `RangeNotSatisfiable` handler (B904).
- `tests/fake_inference.py`: wrapped one 103-char line under 100 (E501).
- ~50 auto-fixed import-sort issues (I001) and a handful of unused-import /
  combined-import fixes (F401/E401) via `ruff check --fix`, all in test files.

`ruff format .` was run once repo-wide (48 files) to establish a formatting
baseline so `ruff format --check` (required by the task) can pass in CI going
forward. This is whitespace/line-wrapping only — no behavior change, and
`pytest -q` (x2) plus frontend checks were re-verified green afterward.

## Concerns / judgment calls

- **Repo-wide `ruff format` run**: the task said to avoid "reformatting the
  whole repo in a CI commit" when a *lint rule* would require it, offering
  rule-disable as the alternative. There's no equivalent per-rule escape for
  the *formatter* (it's binary: matches or doesn't), and the task explicitly
  requires `ruff format --check` in CI, so I ran the formatter once to
  establish a baseline rather than leave that job permanently red. Flagging
  this explicitly — if a smaller diff is preferred, dropping the
  `ruff format --check` step (keeping only `ruff check`) is the alternative.
- **MinIO health check**: GH Actions service containers use the image's
  default entrypoint/CMD as-is (no way to pass `server /data` like
  docker-compose does), so I health-check the HTTP port instead. This is a
  divergence from `docker-compose.test.yml`'s `command:` but was the only way
  to get a usable health check under GH Actions' `services:` model — the
  MinIO server itself still runs via the image default and serves on 9000.
- No Playwright/e2e job, per instructions — noted with a comment at the
  bottom of `quality.yml`.
- Did not modify any application/business logic beyond the two genuine lint
  fixes above.
