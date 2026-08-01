# Ingest paths beyond upload

Uploading a file from the meetings list (see the user guide's
[Uploading a recording](user-guide.md#uploading-a-recording)) is one of
four ways audio reaches the pipeline. [Browser capture](user-guide.md#recording-live-in-the-browser)
is user-driven and documented in the user guide. The other two,
watch-folder and meeting-bot ingest, are admin-configured and covered here.

## Watch-folder ingest

`GET`/`PUT /watch-folders` (`src/meeting_mgr/api/watch_folders.py`) let an
admin register a directory that gets scanned periodically for new
recordings, instead of requiring someone to upload each file by hand.

- **Admin-only.** `PUT /watch-folders` and `GET /watch-folders` both
  `require_role(account, {"admin"})`.
- **`root_path` must be an absolute path.** `PUT` rejects a relative path
  with `422` — a relative path's meaning depends on the scanner process's
  own working directory, which can change across restarts/deployments and
  would otherwise silently point at the wrong directory.
- **`owner_account_id`** must belong to the caller's own organization
  (`422` otherwise) — every Meeting the scanner creates from that folder is
  owned by this Account.
- **`enabled`** (default `true`) turns scanning for that folder on or off
  without deleting the registration.
- **Scanning runs on a fixed interval** (`SCAN_INTERVAL_SECONDS`, in
  `src/meeting_mgr/pipeline/watch_config.py`) via a Celery-beat task
  (`scan_watch_folders`, `src/meeting_mgr/pipeline/watch.py`).
- **`stalled`** in the `GET` response is `true` once two scan intervals
  have passed with no successful scan (`last_scan_at`, or `created_at` if
  it has never scanned) for a folder that's still `enabled` — this is your
  signal that the scanner process has stopped working, not just that the
  folder is empty.
- **`last_scan_error`** carries the most recent scan failure, if any (e.g.
  a directory that no longer exists or isn't readable by the scanner
  process).

## Meeting-bot ingest

Meeting-bots (automated participants that join a call and stream audio) are
a distinct principal type — not an Account — authenticated by a long-lived
bearer credential rather than a login session.

**Minting a credential** — `POST /bot-credentials`
(`src/meeting_mgr/api/bot_credentials.py`), admin-only, body
`{"label": "...", "owner_account_id": <id>}` where `owner_account_id` must
belong to the caller's own organization (`422` otherwise; every Meeting the
bot creates is owned by this Account).

> **The response's `token` field is shown exactly once, at creation, and is
> unrecoverable afterwards.** Only a salted PBKDF2 hash of it is stored
> (`src/meeting_mgr/bot_credentials.py`) — there is no "view token again"
> endpoint and no way for Meeting-MGR to recover it for you. Copy it
> somewhere safe immediately, or you'll need to revoke and reissue.
> **It also never expires** — see the [security status](security.md) page.

**Listing** — `GET /bot-credentials`, admin-only, returns id/label/owner/
`revoked_at`/`created_at` for every credential in your organization (never
the token itself, even for an already-minted one).

**Revoking** — `POST /bot-credentials/{id}/revoke`, admin-only, idempotent
(revoking an already-revoked credential just updates `revoked_at` again and
still returns success). Revocation is the only way to invalidate a leaked
or retired token.

**Bot-side session lifecycle** (`src/meeting_mgr/api/bot.py`, used by the
bot process itself, not an admin): `POST /bot/sessions` starts a session
and creates a Meeting in `capturing` status (idempotent per
`platform_meeting_id` — a retried start returns the existing session
rather than creating a duplicate Meeting); `PUT /bot/sessions/{id}/chunks/
{seq}` uploads audio chunks; `POST /bot/sessions/{id}/finish` closes the
session, builds the manifest, and enqueues the transcription pipeline the
same way browser capture does.

**Stale-session sweep:** if a bot session goes quiet — no chunk upload for
`STALE_SESSION_SECONDS` (4 hours, `src/meeting_mgr/pipeline/bot_config.py`)
— a Celery-beat task (`sweep_stale_bot_sessions`, running every
`BOT_SWEEP_INTERVAL_SECONDS` = 15 minutes) automatically marks the Meeting
`failed` (`failed_stage="bot_ingest"`). If you see a meeting stuck in
`capturing` for longer than 4 hours and 15 minutes, this sweep should have
already caught it — check whether the sweep task itself is running before
assuming the bot is still connected.
