# CI/CD status — verified on real runners, 2026-07-31

All four workflows pass on GitHub Actions against commit cf65973.

| Workflow | Result | What it actually proves |
|---|---|---|
| quality  | pass | Full backend suite run TWICE against live Postgres/Redis/MinIO with ffmpeg + alembic migrations; ruff check and format; frontend vitest, tsc, production build |
| security | pass | uv audit, npm audit (GHSA-qwww-vcr4-c8h2 allowlisted by id per issue #34), Trivy HIGH/CRITICAL on api+web images, gitleaks over full history, CodeQL python + javascript-typescript |
| container| pass | api, web AND diarizer images built and pushed to ghcr.io/sgtslaughta/meeting-mgr/* with sha + latest tags |
| docs     | pass | zensical build --strict deployed to Pages; https://sgtslaughta.github.io/meeting-mgr/ returns 200, user-guide and admin-guide both 200 |

## Three real bugs the first runs caught, all invisible to local checks

1. **MinIO as a service container never starts.** GH Actions `services:` cannot override a container CMD, and minio/minio's CMD is ["minio"] with no subcommand -- it prints usage and exits, so the health check can never pass. Verified by running the bare image (exits immediately) vs `server /data` (ready in 1s). Now started via docker run in a step. Fixed in 3129f4e.
2. **Pages was never enabled on the repo.** actions/configure-pages failed with "Get Pages site failed ... Not Found". Enabled via API with build_type=workflow AND set enablement: true so a fresh fork does not need a manual settings visit. Fixed in 61950be.
3. **aquasecurity/trivy-action@v0.28.0 pins a setup-trivy version that no longer exists.** Failed with "Unable to resolve action aquasecurity/setup-trivy@v0.2.1". Replaced with a direct `docker run aquasec/trivy` -- one fewer dependency edge, and exactly what had been verified locally. Fixed in cf65973.

The common thread: every one passed actionlint, YAML validation, and action-tag existence checks. Local verification cannot see repository state, a base image's CMD, or a third-party action's own internal pins.

## Deliberately NOT covered by CI, and must not be claimed as covered

- **The real pyannote diarizer has never been exercised end to end.** No HF_TOKEN. The image now builds in CI, but nothing runs it. Everything downstream of diarization was proven against a stand-in stub matching the /diarize contract.
- **The Playwright e2e spec does not run in CI** -- it needs the full stack including that diarizer. It passed locally against a real stack with a stubbed diarizer. There is a comment saying so in quality.yml.
- **Live SSE traffic through the nginx proxy** verified only by nginx -t and config inspection.
