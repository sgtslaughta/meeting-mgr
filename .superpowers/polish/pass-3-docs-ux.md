# Polish pass 3 — documentation and user-facing consistency

Scope: `docs/`, `zensical.toml`, `CONTEXT.md`, root `CLAUDE.md`. Analysis only,
no edits made. Verified claims against code on `main` @ `980c039` (Phase 6
already merged to main — docs were not updated alongside it).

Severity legend: **Critical** = actively misleading, could cause data loss or
a security mistake. **Important** = a user or admin cannot complete a real
task. **Minor** = polish.

---

## Critical

### 1. `docs/admin-guide.md` "Security status — read this first" describes a system that no longer exists
The entire section (lines 6–110) is written as of Phase 3 *in progress*:
"Authentication and authorization are being added in this phase (Phase 3) and
are not yet complete... assume the deployment is not fully protected." This is
false on `main` today. Evidence:
- `src/meeting_mgr/authz.py` implements the single authorization chokepoint
  (`authorize()`, `require_role()`, `readable_meetings_filter()`), applied
  across `meetings.py`, `capture.py`, `watch_folders.py`,
  `retention.py`, `bot_credentials.py`, `audit_log.py`.
- `src/meeting_mgr/api/auth.py` has working OIDC, local password, and (per
  its module comment) mTLS login, session cookies, `/auth/me`.
- Git history: `1aa7b3b feat: phase 3 authentication, authorization and
  tenancy`, `794ade4 feat: add the single authorization chokepoint`, plus
  three more Phase-3 auth commits, all already on `main`.

An admin reading this section today is told to treat *every* endpoint as
unauthenticated and to hide the whole deployment behind a VPN — overly
cautious in the way that erodes trust in the rest of the doc, and it buries
the specific, still-true caveats (OIDC default-org, upgrade-migration
fail-closed note) under a blanket "nothing is protected" framing that no
longer matches reality.

**Fix direction:** rewrite the section to state auth/authz/tenancy/RLS are
implemented (Phase 3 shipped), keep the OIDC-default-org and
upgrade-fail-closed subsections (still accurate, see below), and reframe
remaining caveats precisely instead of "not yet complete."

### 2. "Child artifact tables have no row-level-security policy" is false — the gap was closed
`docs/admin-guide.md` lines 92–110 states child tables (`segment`,
`key_topic`, `minute`, `action_item`, `decision_point`, `speaker_cluster`,
`attribution`) "get no policy of their own" and that "an authorization bug in
the API would expose transcript segments... across tenants," citing issue
#35 as open.

This is now false. `migrations/versions/c4d8e2f1a6b3_rls_child_tables.py`
("Closes the gap tracked in issue #35") adds `ENABLE ROW LEVEL SECURITY` and
a `tenant_isolation` policy to `segment`, `key_topic`, `minute`,
`action_item`, `decision_point`, `speaker_cluster`, `recording`,
`meeting_share`, and `attribution` (chained through `speaker_cluster`). This
migration is already applied on `main` (it's `down_revision` of the RLS-enable
migration, i.e. part of the same lineage, and predates the bot/retention/watch
migrations that are also on `main`).

This is the single most consequential inaccuracy in the docs: it tells an
operator "the database has no backstop here, budget for that risk" when the
backstop now exists. Leaving it in place doesn't cause data loss directly,
but it actively misinforms a security-conscious operator's risk assessment
and points them at a closed GitHub issue as if it were open.

**Fix direction:** remove or replace the section — either delete it (if RLS
now covers everything, per the migration's own docstring, which claims
verified empirical coverage including a two-level chain) or replace it with
a short confirmation that RLS now covers all tenanted tables including
children, if pass-1/pass-2 didn't independently re-verify the claim.

### 3. `docs/index.md` "Read this before you deploy it anywhere reachable" is stale and contradicts the rest of the site
Lines 41–48: "Meeting-MGR has **no authentication or authorization yet**. Any
caller that can reach the API can read and change any meeting... tracked as
Phase 3... and not built." Same defect as Finding 1, on the landing page —
the first thing a new reader sees. It also links to the admin-guide's
Security status section, propagating the same stale claim.

**Fix direction:** replace with an accurate one-paragraph summary of current
auth (OIDC/password/mTLS, roles, per-meeting visibility, RLS) plus a pointer
to whatever caveats remain after Finding 1/2 are resolved.

---

## Important

### 4. Meeting-bot ingest is entirely undocumented
Phase 6 shipped `POST /bot-credentials` (mint), `GET /bot-credentials` (list),
`POST /bot-credentials/{id}/revoke` (revoke), and the bot-facing
`/bot/sessions/*` endpoints (`src/meeting_mgr/api/bot_credentials.py`,
`src/meeting_mgr/api/bot.py`). None of it appears in `docs/admin-guide.md` or
`docs/user-guide.md` — `grep -i "bot"` across both files returns nothing
except unrelated words. This is exactly the gap the task brief flagged as
likely. Missing, specifically:
- How to mint a credential: `POST /bot-credentials` is **admin-only**
  (`require_role(account, frozenset({"admin"}))`), body is
  `{label, owner_account_id}` where `owner_account_id` must belong to the
  caller's own organization (422 otherwise).
- **The plaintext token is returned exactly once**, in the create response's
  `token` field, and is never recoverable afterwards — only its PBKDF2 hash
  is stored (`src/meeting_mgr/bot_credentials.py`: "the plaintext token is
  never stored; only its hash is"). This needs to be an explicit,
  can't-miss callout — an admin who loses the token has no recovery path
  short of revoke-and-reissue.
- Revocation: `POST /bot-credentials/{id}/revoke`, admin-only, idempotent.
- **The token has no expiry** (`BotCredential` model has no expiry column) —
  revocation is the *only* mitigation if a token leaks. This belongs
  alongside the `SESSION_SECRET` security note in the admin guide, in the
  same register as the react-router advisory / RLS-gap callouts.
- Operational fact: a stale bot session (no chunk upload for 4 hours —
  `STALE_SESSION_SECONDS = 14400` in `pipeline/bot_config.py`) is
  automatically failed by `sweep_stale_bot_sessions`, a Celery-beat task
  running every 15 minutes (`BOT_SWEEP_INTERVAL_SECONDS = 900`). An admin
  troubleshooting a meeting stuck in `capturing` needs to know this sweep
  exists and its timing.

**Fix direction:** new "Meeting-bot ingest" subsection in the admin guide
(credential lifecycle is an admin/operator concern) covering mint, the
one-time-token warning, revoke, no-expiry risk, and the stale-session sweep.

### 5. Watch-folder ingest is entirely undocumented
`POST/GET /watch-folders` (`src/meeting_mgr/api/watch_folders.py`) is
admin-only, requires an absolute `root_path` (422 if relative — "meaning
depends on the scanner process's working directory"), and reports a
`stalled` flag when two scan intervals (2 × 300s = 10 minutes) pass without a
scan. None of this — including that the feature exists at all — is in either
guide. This is an admin-configured ingest path (comparable to the
`docker-compose.yml`/env-var material already in the admin guide) and belongs
there.

### 6. Browser-capture ingest is entirely undocumented
`POST /meetings/capture` + chunk upload (`src/meeting_mgr/api/capture.py`) is
open to `admin` or `member` roles (`_CAN_CAPTURE = frozenset({"admin",
"member"})`) — this is the one ingest path an ordinary end user drives
themselves, so it belongs in the **user guide**, not the admin guide, and
today it's in neither. The user guide only documents the upload-a-file path
(`## Uploading a recording`); a user has no way to learn that live
browser-capture recording exists.

### 7. Retention/purge admin workflow is entirely undocumented
`GET/PUT /retention-policy`, `GET /retention-policy/preview`, `POST
/retention-policy/purge` (`src/meeting_mgr/api/retention.py`) are all
admin-only and control **irreversible deletion** — yet neither guide
mentions retention policy configuration, the preview-before-you-purge
endpoint, or the daily `sweep_retention` background purge. This is squarely
the kind of gap the task brief calls "irreversible data loss" territory:
right now the only way to learn the `NULL`=keep-forever / `0`=purge-now
semantics is to read `src/meeting_mgr/retention.py` source comments — no
operator should have to do that before touching a delete-everything lever.
Specifically worth documenting:
- `audio_retention_days` / `meeting_retention_days`, both `int | None`.
  `None` (omit from the PUT body, or explicit `null`) = keep forever. `0` is
  a legitimate, distinct value = purge immediately (confirmed by the
  validator's own comment: "0 ('purge immediately') is a legitimate, distinct
  value from NULL ('keep forever')" — reject only negative values).
  `audio_retention_days` must not exceed `meeting_retention_days` (422 if it
  does).
  A meeting past `meeting_retention_days` gets a **full purge**; past only
  `audio_retention_days` gets an **audio-only purge** (raw/normalized
  recording deleted, everything else kept).
- `GET /retention-policy/preview` is a dry-run — same query as the real
  purge, so "what will this delete" is answerable before committing to a
  policy change. This should be advertised as the safe way to change a
  retention policy, front and center.
- `POST /retention-policy/purge` triggers an immediate async purge
  (`202`, Celery `purge_organization.delay(...)`) independent of the daily
  scheduled sweep.
- Provenance (`confirmed` vs `inferred`) does **not** protect anything from
  purge — the retention module's own docstring says so explicitly. This is a
  natural point of confusion (a user might assume "I confirmed this, surely
  it's safe") and deserves a one-line warning in the admin guide.

**Fix direction:** new "Retention and purge" section in the admin guide,
adjacent to "Backup and restore" — same operational register, and the two
interact (a purge is not something you can undo from a backup taken after it
ran, without careful handling).

### 8. Admin vs. user split: browser capture is filed under the wrong (missing) audience
Restating Finding 6 as an audience-split defect per the task's explicit
question 4: browser capture is a task an ordinary `member` performs
themselves through the UI — it belongs in `docs/user-guide.md`'s ingest
material, not folded into admin-only content once written. Everything else
newly missing (bot credentials, watch folders, retention) is correctly
admin-scoped and belongs in `docs/admin-guide.md`.

### 9. Getting-started path is not actually walkable end to end
Per the task's question 6: a new self-hoster following only `docs/index.md`
+ `docs/admin-guide.md` hits `docker compose up` and the environment-variable
table, but there is **no explicit "first run" walkthrough** — no sequence of
"clone → set `HF_TOKEN` and inference env vars → `docker compose up` → open
`http://localhost:5173` → upload a file → watch it process." The admin guide
has all the *pieces* (architecture table, env vars, `HF_TOKEN` section) but
never assembles them into a single ordered path, and it never mentions the
first authentication step at all: with Phase 3 auth now real, **a fresh
deployment has no bootstrapped admin Account** documented anywhere — how does
the very first operator log in to create the first Organization/Account?
Concretely: the first blocking step for a new self-hoster is "how do I get
credentials to log in the first time," and neither guide answers it. If
there's a seed script, CLI command, or first-run bootstrap path in the code,
it needs to be surfaced in the admin guide as literally the first
post-`docker compose up` step; if there isn't one, that's a product gap for
a fixes-pass to flag, not a docs gap.

---

## Minor

### 10. `docs/superpowers/` (internal plans/specs) is built and published to the public docs site
`docs/superpowers/phase-1-deferred.md`, `docs/superpowers/plans/*.md`
(six phase plans), and `docs/superpowers/specs/2026-07-31-meeting-mgr-design.md`
are not in `zensical.toml`'s `nav`, but `zensical build --strict` (verified in
a throwaway worktree, not the main checkout) still renders them into
`site/superpowers/...` and they will be live and crawlable on GitHub Pages —
`zensical` does not fail strict mode on orphaned/un-navved pages, it just
silently publishes them outside the nav tree. These read as internal
planning artifacts (phase plans, a design spec) rather than intended public
documentation. Not a CI break, but worth a deliberate decision: either add
`docs_exclude`/move them outside `docs/` if they're not meant to be public,
or add them to `nav` if they are.

### 11. `docs/agents/*.md` and `docs/adr/*` are unaffected by phases 3–6 and did not need changes
Checked `docs/agents/domain.md`, `issue-tracker.md`, `triage-labels.md`, and
all three ADRs for staleness against the current codebase — no defects
found; these are process/architecture docs that don't describe
phase-specific product surface, so they aged fine. Noted here only so the
fixer doesn't re-check them.

### 12. `CONTEXT.md` (`Ingest Adapter` glossary entry) is already current
Already defines Ingest Adapter as "upload, watch folder, browser capture, or
meeting bot" — Phase 5/6 vocabulary is present. No action needed; called out
so this doesn't get miscounted as another doc needing a Phase-6 pass.

---

## Summary of what was and wasn't verified
- Verified against code on `main`, not against an assumption of "docs are
  probably behind": read `authz.py`, `auth.py`, `retention.py` (api +
  service), `bot_credentials.py` (api + service), `bot.py` (api + pipeline
  sweep), `watch_folders.py`, `capture.py`, `bot_config.py`,
  `watch_config.py`, and the RLS migrations directly.
- Ran `zensical build --strict` in an isolated `git worktree --detach` (never
  in the main checkout), confirmed it currently passes with "No issues
  found" — so today's docs tree is not a CI-break risk on the build-mechanics
  axis, only on the content-accuracy axis covered above.
- Did not re-verify pass-1/pass-2 territory (code correctness, security
  logic itself) beyond what was needed to confirm or refute a documentation
  claim.
