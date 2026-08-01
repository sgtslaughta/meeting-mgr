# Admin guide

For whoever deploys and operates Meeting-MGR. If you want to know what a user
sees, read the [user guide](user-guide.md) instead.

## Security status — read this first

**Authentication, authorization, and tenancy are implemented and shipped on
`main`,** backed by Postgres row-level security as a database-level
backstop, not just an application-layer check. See the
[Security status](security.md) page for the full picture: what's enforced,
the OIDC default-organization caveat, the upgrade-migration note, and the
handful of caveats that remain (an unexpiring bot bearer token, the react-
router advisory, superuser DB credentials during identity bootstrap).

## Architecture

Four deployable units, plus inference endpoints you provide yourself:

| Container | Image basis | Responsibility | Needs a GPU? |
| --- | --- | --- | --- |
| `api` | root `Dockerfile` (`python:3.12-slim` + ffmpeg) | FastAPI HTTP surface: upload, list/read meetings, SSE progress, edit/delete/regenerate artifacts, confirm attributions | No |
| `worker` | same image as `api` | Celery worker running the seven pipeline stages | No |
| `diarizer` | `services/diarizer/Dockerfile` | Its own FastAPI service exposing `POST /diarize`, wrapping `pyannote.audio` | Yes — the only container that does |
| `web` | `web/Dockerfile` (Node build → `nginx:alpine`) | Static React SPA, plus an nginx reverse proxy that forwards `/meetings*` to `api` | No |

Backing services, all defined in `docker-compose.yml`:

- **Postgres 17** — the system of record (meetings, segments, clusters,
  attributions, artifacts, participants).
- **Redis 7** — the Celery broker, and also the pub/sub channel the API uses
  to stream stage-transition events to the SSE endpoint (`progress.py`).
  Started with `--appendonly yes` for durability: a lost queued job can mean
  redoing an hour of GPU-bound diarization work.
- **MinIO** — S3-compatible object storage for raw and normalized
  recordings. Any S3-compatible endpoint works; MinIO is just what the
  compose stack ships.

The API and worker images carry **no `torch` and assume no GPU** — see
[ADR-0001](adr/0001-inference-over-http.md). Diarization is deliberately
isolated in its own container for exactly this reason — see
[ADR-0002](adr/0002-diarization-as-owned-service.md).

## Deployment via docker-compose

```
docker compose up
```

Startup order is enforced by `depends_on`/healthchecks in
`docker-compose.yml`:

1. `postgres` starts and must pass `pg_isready`.
2. `migrate` runs `alembic upgrade head` once and exits `0`. `api` and
   `worker` wait for this to *complete successfully* before starting, so a
   fresh stack never serves against an empty schema and two replicas can't
   race two migrations.
3. `api`, `worker`, `minio`, `redis` come up.
4. `diarizer` requires `HF_TOKEN` (see below) and an NVIDIA GPU
   (`deploy.resources.reservations.devices` requests one).
5. `web` builds the SPA and serves it via nginx, proxying `/meetings*` to
   `api:8000`.

Ports exposed by the default compose file: `8000` (API), `5173` (web,
mapped from nginx's `80`), `8081` (diarizer), `9000`/`9001` (MinIO
API/console).

`ASR_BASE_URL`/`ASR_API_KEY`/`ASR_MODEL`/`LLM_BASE_URL`/`LLM_API_KEY`/
`LLM_MODEL` default in the compose file to
`http://host.docker.internal:8080/v1` — this only resolves automatically on
Docker Desktop. On plain Linux Docker the compose file adds
`extra_hosts: ["host.docker.internal:host-gateway"]` to `api`/`worker`/
`migrate` so that default still reaches an inference server running on the
host; if your inference endpoint lives elsewhere, override these variables.

## Getting started: from a clone to a first transcribed meeting

1. Clone the repo and set the environment variables `docker-compose.yml`
   requires explicitly — at minimum `SESSION_SECRET` (see
   [Environment variables](#environment-variables) below) and `HF_TOKEN`
   (see [The diarizer service](#the-diarizer-service-and-hf_token)). Point
   `ASR_*`/`LLM_*` at a real inference endpoint if you're not relying on the
   `host.docker.internal` default.
2. Run `docker compose up`. Wait for `migrate` to exit `0` and `api`/`worker`/
   `web` to report healthy — see [Deployment via docker-compose](#deployment-via-docker-compose)
   above for the startup order.
3. Open `http://localhost:5173` (the `web` container's nginx, proxying
   `/meetings*` to `api:8000`).

### The first admin login is a real gap, not a documentation gap

**As of this version, there is no bootstrap mechanism that creates a first
Account.** This was verified directly against the code, not assumed:

- The only migration that seeds any row is `0001_initial.py`, which inserts
  one `Organization` named `default` — no `Account` row, ever.
- There is no `POST /accounts` (or any other) endpoint that creates an
  Account. `grep -rn "Account(" src/meeting_mgr` outside of tests turns up
  exactly one place a new `Account` is constructed: `GET /auth/oidc/
  callback` in `src/meeting_mgr/api/auth.py`, which auto-provisions an
  Account the first time an OIDC subject logs in — always with
  `role="member"`, never `admin`.
- Local password login (`POST /auth/login`) and mTLS only ever *match* an
  existing Account; neither can create one.

**Practical consequence:** if you configure OIDC, your first login creates
a `member` Account in the `default` organization — not an admin. If you
don't configure OIDC (local-password-only or mTLS-only deployment), there
is currently no way to reach a first login at all through the product's own
surface.

**The only way in today** is to create the first Account directly in
Postgres. For a password-login admin:

```
docker compose exec api python -c "
from meeting_mgr.auth.password import hash_password
print(hash_password('choose-a-real-password'))
"
```

then insert a row with that hash and `role='admin'` into the `default`
organization (`psql`, connected as the superuser `DATABASE_URL`, not the
least-privilege `meeting_app` role):

```sql
INSERT INTO account (organization_id, email, role, password_hash)
SELECT id, 'you@example.com', 'admin', '<hash from above>'
FROM organization WHERE name = 'default';
```

If you're using OIDC, log in once to auto-provision your `member` Account,
then promote it from `psql`:

```sql
UPDATE account SET role = 'admin' WHERE email = 'you@example.com';
```

This is a product gap, not an intended workflow — flag it if you'd rather
see a proper bootstrap command or first-run admin-invite flow.

### Then: your first meeting

Once you can log in as an admin, upload a recording from the meetings list
page (see the user guide's [Uploading a recording](user-guide.md#uploading-a-recording))
and watch it move through the pipeline. Watch-folder and meeting-bot ingest
(below) are alternative, admin-configured ways to get audio in — you don't
need either one for a first end-to-end run.

## Environment variables

Read from `src/meeting_mgr/config.py` (a `pydantic-settings` `Settings`
class — variable names are the uppercased field names, matched
case-insensitively).

**The defaults below are test/development values pointing at local ports.
They are not suitable for production** — the database password is
literally `test`, and every URL points at `localhost`. `docker-compose.yml`
overrides all of them with its own (also weak, e.g. MinIO's
`meeting`/`meetingmeeting`) values for the compose stack; you should
override them again with real secrets for any real deployment.

| Variable | Default (code) | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://postgres:test@localhost:55432/meeting_mgr_test` | Postgres connection string, `psycopg` v3 driver |
| `REDIS_URL` | `redis://localhost:56379/0` | Celery broker + progress pub/sub |
| `S3_ENDPOINT` | `http://localhost:59000` | S3-compatible endpoint for recordings |
| `S3_ACCESS_KEY` | `test` | S3 access key |
| `S3_SECRET_KEY` | `testtest` | S3 secret key |
| `S3_BUCKET` | `recordings` | Bucket name; created automatically on first upload if missing |
| `ASR_BASE_URL` | `http://localhost:58080/v1` | OpenAI-compatible base URL used for transcription |
| `ASR_API_KEY` | `unused` | API key sent to the ASR endpoint |
| `ASR_MODEL` | `whisper-1` | Model name sent in ASR requests |
| `LLM_BASE_URL` | `http://localhost:58080/v1` | OpenAI-compatible base URL used for attribution and artifact extraction |
| `LLM_API_KEY` | `unused` | API key sent to the LLM endpoint |
| `LLM_MODEL` | `local-model` | Model name sent in LLM requests |
| `DIARIZER_URL` | `http://localhost:58081` | Base URL of the diarizer service (no `/diarize` suffix) |
| `SESSION_SECRET` | `INSECURE-DEV-SESSION-SECRET-DO-NOT-USE-IN-PRODUCTION` | Signs the session cookie (`itsdangerous`, via Starlette's `SessionMiddleware`). **You must override this in any real deployment** — anyone who knows the default can forge a session cookie and log in as any Account. Set it to a long random value (e.g. `openssl rand -hex 32`) and keep it secret; rotating it invalidates all existing sessions. |

`SessionMiddleware` is currently configured with its default `https_only=False`,
which is the right default for a plain-HTTP self-hosted deployment (it lets the
session cookie work without TLS out of the box). **If you terminate TLS in
front of the API** (a reverse proxy, load balancer, or `web`'s nginx doing
HTTPS), set `https_only=True` where `SessionMiddleware` is added in
`src/meeting_mgr/api/main.py` so the cookie gets the `Secure` flag — without
it, the session cookie can be sent in the clear over HTTP if a caller or a
misconfigured redirect ever reaches the plain-HTTP origin.

`HF_TOKEN` is **not** one of these — it belongs to the separate `diarizer`
container/process, not the API/worker `Settings` class. See below.

## Pointing at an inference endpoint

Meeting-MGR never bundles a model. It calls an OpenAI-compatible HTTP
endpoint for both ASR (`ASR_*`) and LLM (`LLM_*`) work — see
[ADR-0001](adr/0001-inference-over-http.md). This is deliberate: the app
runs identically on a laptop and a GPU box, and "which model" becomes
operator configuration (`base_url`, `api_key`, `model`), not a code choice.

Anything that speaks the OpenAI wire format works: vLLM, llama.cpp, Ollama,
LM Studio, LiteLLM as a router, or a commercial API. ASR and LLM can point
at two different endpoints and models — they're configured independently.

Every structured call (attribution, the four extraction passes) validates
the response against a Pydantic schema and retries with bounded backoff,
because an inference endpoint is a network call that can time out,
rate-limit, or return malformed JSON — never assume it's reliable just
because it's "local."

## The diarizer service and `HF_TOKEN`

The `diarizer` container wraps `pyannote.audio`'s
`pyannote/speaker-diarization-3.1` pipeline and `pyannote/embedding` model.
Both are gated on Hugging Face: you need an account, a token
(https://huggingface.co/settings/tokens), and to accept each model's terms
before the service can load them. Set `HF_TOKEN` in the environment the
`diarizer` container runs in — `docker-compose.yml` requires it explicitly
(`${HF_TOKEN:?HF_TOKEN is required for pyannote}`) and refuses to start
without it.

The service needs an NVIDIA GPU reachable through the Docker runtime; the
compose file requests one via `deploy.resources.reservations.devices`. It
returns Speaker Clusters *and* voice embeddings for each — see
[ADR-0002](adr/0002-diarization-as-owned-service.md) for why embeddings are
stored even though v1 only does manual attribution.

**Honesty check, important for anyone about to depend on this:** the real
pyannote-backed diarizer has never been run end-to-end in this project's
development. No `HF_TOKEN` was available in that environment. Everything
downstream of diarization — alignment, attribution, extraction, the review
UI — was built and proven against a stand-in stub that matches the
`/diarize` HTTP contract (same request/response shape), not against real
pyannote output. If you deploy this, the diarizer path is the
**least-exercised part of the system**. Budget time to validate it against
your own recordings before trusting it in production, and watch for
diarization-stage failures specifically.

## Ingest paths beyond upload

Uploading a file from the meetings list is one of four ways audio reaches
the pipeline. Watch-folder ingest and meeting-bot ingest are admin-
configured and covered on the [Ingest paths](ingest.md) page — including
the mint/list/revoke lifecycle for bot credentials, the one-time-token
warning, and the stale-session sweep. Browser capture is user-driven and
documented in the [user guide](user-guide.md#recording-live-in-the-browser).

## Storage (MinIO/S3) and the database

- **Object storage** holds two keys per meeting: `raw/{meeting_id}/{filename}`
  (exactly what was uploaded) and `normalized/{meeting_id}.wav` (16 kHz mono,
  produced by the `normalize` stage). `ensure_bucket()` creates the bucket on
  first use if it's missing — it does not otherwise manage bucket
  lifecycle/policy for you.
- **Postgres** is the system of record for everything else: meetings,
  recordings' metadata, segments, speaker clusters (including their
  embeddings), attributions, participants, and the four artifact tables
  (key topics, minutes, action items, decision points).

### Voice embeddings are biometric data — a compliance note

Speaker Cluster embeddings, stored in Postgres, are voice biometric data
under GDPR Article 9. The application **deliberately never exposes them
through the API** — the `_FIELDS` allowlist in `api/meetings.py` that
governs what a `SpeakerCluster` serializes to omits `embedding` on purpose.
Storing them is a conscious tradeoff (it's what would let automatic
voiceprint matching become a feature toggle later instead of a
re-processing job over your whole archive — see ADR-0002) but it is a real
compliance obligation you take on as the operator, not something the
software has to solve for you. If you have data-retention or Art. 9
obligations, plan for it: know that this data exists, where (`speaker_
cluster.embedding`), and that it's purged when a meeting is hard-deleted.

## Running migrations

Migrations are Alembic, configured at the repo root (`alembic.ini`,
`migrations/`). In the compose stack, the one-shot `migrate` service runs
`alembic upgrade head` automatically before `api`/`worker` start — you don't
normally run this by hand.

To run it manually (e.g. against a deployment outside compose):

```
alembic upgrade head
```

New migration after a model change:

```
alembic revision --autogenerate -m "describe the change"
```

Review the generated file before applying it, as always with autogenerate.

## Retention and purge

`GET`/`PUT /retention-policy`, `GET /retention-policy/preview`, and
`POST /retention-policy/purge` control **irreversible deletion** — the
`null`-keeps-forever / `0`-purges-immediately semantics, the difference
between a full purge and an audio-only purge, the dry-run preview, and why
provenance doesn't protect anything from a purge are all covered in full on
the [Retention and purge](retention.md) page. Read it before you configure
a policy or trigger a purge — there is no undo.

## Backup and restore

Meeting-MGR ships no backup tooling of its own — back up the two things that
hold real state, the same way you'd back up any Postgres + S3 application:

- **Postgres** (the `pgdata` volume): `pg_dump`/`pg_restore`, or snapshot the
  volume with the database stopped or in a consistent snapshot mode.
- **Object storage** (the `miniodata` volume, or your S3-compatible
  endpoint): mirror the bucket (`mc mirror`, `aws s3 sync`, or your
  provider's native replication).

Because raw and normalized recordings live in object storage and everything
*derived* from them lives in Postgres, the two must be backed up together to
stay consistent — an out-of-sync restore can leave a meeting record pointing
at recording keys that no longer exist (a state the product already
supports gracefully for retention-driven purges, but not one you want by
accident).

Redis holds only the Celery queue and a short-lived pub/sub channel for
progress events — it's disposable. `--appendonly yes` is set in the compose
file only to protect in-flight jobs across a container restart, not as a
long-term durability guarantee; it isn't part of your backup set.

## Troubleshooting

- **A pipeline stage fails.** The meeting's `failed_stage` is recorded and
  surfaced in the UI (and in `GET /meetings/{id}`). Everything produced by
  earlier stages is retained and usable — a transcript with no action items
  is a normal partial-success state, not corruption. As of this version
  there is **no HTTP endpoint to resume a failed pipeline stage** (only
  artifact *regeneration* — `key_topics`/`minutes`/`action_items`/
  `decision_points` — is exposed via `POST /meetings/{id}/regenerate/
  {artifact_type}`). The underlying Celery task does support resuming from a
  named stage (`run_pipeline(meeting_id, from_stage=...)` in
  `pipeline/orchestrate.py`), but today you'd invoke that yourself (e.g. from
  a Python shell in the `worker` container) rather than through the API.
- **`ffmpeg` failures.** The `normalize` stage runs `ffmpeg`/`ffprobe`
  against the uploaded file; a corrupt or unsupported file fails with the
  tail of `ffmpeg`'s actual stderr in the recorded error, so the message
  should tell you what's wrong with the specific file rather than a generic
  "pipeline failed."
- **`diarizer` won't start.** Almost always a missing/invalid `HF_TOKEN`, or
  the Hugging Face account hasn't accepted the model terms for
  `pyannote/speaker-diarization-3.1` and `pyannote/embedding` — both are
  required, not just one.
- **Live progress (SSE) behind the nginx proxy.** `web/Dockerfile` sets
  `proxy_buffering off` on the `/meetings` location specifically so
  Server-Sent Events reach the browser as they're published rather than
  being buffered by nginx. **This has been verified only by reading the
  nginx config, not by observing live SSE traffic through the proxy in this
  environment.** If progress updates seem to stall or arrive in a burst at
  the end instead of incrementally, check this first — it's the one part of
  the request path that was reasoned about rather than exercised.
- **Uploads rejected or truncated.** `client_max_body_size 0` and
  `proxy_request_buffering off` are set in the nginx config specifically so
  large recordings aren't capped or fully buffered before reaching the API.
  If you've customized the nginx config, check those two settings first.

## Known accepted risk: react-router advisory

`web/`'s `react-router-dom` dependency pulls in a `react-router` version
affected by a high-severity advisory
([GHSA-qwww-vcr4-c8h2](https://github.com/advisories/GHSA-qwww-vcr4-c8h2),
tracked as
[GitHub issue #34](https://github.com/sgtslaughta/meeting-mgr/issues/34)).
The vulnerable code path is React Server Components (RSC) mode, which this
app — a plain client-side SPA — does not use, so it's currently believed
unreachable in our deployment. The only available fix
(`npm audit fix --force`) downgrades to a breaking `react-router-dom`
version, so this has been deliberately left unresolved pending a decision
rather than silently patched or silently ignored. If you run your own CI
security scanning against this repo, expect it to flag this advisory.
