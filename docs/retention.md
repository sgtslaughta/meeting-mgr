# Retention and purge

`GET`/`PUT /retention-policy`, `GET /retention-policy/preview`, and
`POST /retention-policy/purge` (`src/meeting_mgr/api/retention.py`) control
**irreversible deletion**. Read this page fully before you touch any of
them — there is no undo, and it is not just a summary; the underlying
behavior was read directly from `src/meeting_mgr/retention.py` and
`src/meeting_mgr/pipeline/purge.py`.

All three endpoints are admin-only.

## The policy: two numbers, two meanings

`PUT /retention-policy` takes `audio_retention_days` and
`meeting_retention_days`, both `int | None`:

- **`null` (omit the field, or send it explicitly as `null`) means keep
  forever.** No purge ever happens for that dimension.
- **`0` means purge immediately** — this is deliberately a distinct,
  legitimate value from `null`, not an error. Only *negative* values are
  rejected (`422`).
- **`audio_retention_days` must not exceed `meeting_retention_days`**
  (`422` otherwise) — it makes no sense for audio to outlive the meeting it
  belongs to.

**What each threshold does, once a Meeting is older than it:**

- Past `meeting_retention_days`: a **full purge** — audio objects, the
  Meeting row, and every child artifact (segments, key topics, minutes,
  action items, decision points, speaker clusters, attributions) cascade-
  deleted with it. Nothing about that meeting survives.
- Past only `audio_retention_days` (and not yet past
  `meeting_retention_days`): an **audio-only purge** — the raw and
  normalized recording objects and the `Recording` row are deleted;
  transcript, artifacts, and everything else are kept. Citations still
  resolve to text; "click a citation to hear it" stops working for that
  meeting because there's no audio left to seek to.

A meeting past both thresholds is purged fully, once — it is never
double-counted or double-purged as both "audio" and "full."

## Preview before you purge

**`GET /retention-policy/preview` is a dry-run: it runs the exact same
selection logic the real purge uses**, with no cap on how many results it
returns, and deletes nothing. Treat it as the front door for any policy
change — before tightening a retention policy (or before running a manual
purge), preview it first to see exactly which meetings, and how many
segments/artifacts/attributions each one carries, are about to go away.
The preview and the real purge can never disagree, by construction (same
query, same filters — see `select_purge_candidates()`).

## Triggering a purge

- **`POST /retention-policy/purge`** enqueues an immediate, asynchronous
  purge for your organization (`202`, `purge_organization.delay(...)`),
  independent of the daily scheduled sweep below. Use this after you've
  previewed and are ready to act now rather than waiting for the next
  scheduled run.
- **A daily scheduled sweep** (`sweep_retention`, Celery-beat) finds every
  organization with any retention policy configured and dispatches
  `purge_organization` for each one automatically — you do not need to
  trigger a purge by hand for retention to actually take effect over time.
  Each organization's purge processes a bounded batch per run (500
  candidates of each kind by default), so a large backlog drains over
  several days rather than in one long-running operation.

## Provenance does not protect anything from purge

**`confirmed` vs. `inferred` provenance has no bearing on what gets
purged.** A fact a human reviewed and confirmed is deleted exactly the same
as one nobody ever looked at, once its Meeting crosses the configured
threshold. Don't assume "I confirmed this" means "this is safe" — the only
thing that keeps a meeting from being purged is `null` retention values or
not yet having aged past the configured threshold.

## Purges and backups

A purge is not something you can undo from a backup taken after it ran —
see the admin guide's [Backup and restore](admin-guide.md#backup-and-restore)
section. If you need to recover a purged meeting, the only path is
restoring both Postgres and object storage together from a snapshot taken
*before* the purge, understanding that doing so also reverts anything else
that changed in the meantime.
