# Fix report — pass-3 docs polish

Worktree: `/home/user/code/meeting_mgr/.claude/worktrees/agent-ad770c0efecbcf7d3`
Branch: `worktree-agent-ad770c0efecbcf7d3`
Commit: `1e5027c` (single commit — see `git show 1e5027c --stat`)

## Status: all 3 Critical, 6 Important, 3 Minor findings addressed

`zensical build --strict` passes ("No issues found") after every change,
including new-page nav wiring and cross-file anchor links (spot-checked
against the built `site/` output, not just build exit code).

## What changed

- **docs/security.md (new)** — replaces the stale "Phase 3 not yet
  complete" security section. States auth/authz/tenancy/RLS as shipped,
  keeps the still-accurate OIDC-default-org and upgrade-migration
  subsections, replaces the "child tables have no RLS" claim with
  confirmation that RLS now covers them (verified against migration
  `c4d8e2f1a6b3_rls_child_tables.py`), and adds two new caveats found
  while verifying: bot bearer tokens never expire, and the `api` process
  holds superuser DB credentials for identity bootstrap (issue #37).
- **docs/index.md** — landing-page deploy warning rewritten to match;
  links to the new security page.
- **docs/admin-guide.md** — security section replaced with a pointer to
  security.md; added "Getting started" walkthrough (see gap below); added
  pointer sections to the new ingest.md/retention.md pages. Net effect:
  file dropped from 632 lines (an intermediate, unsplit draft) to 375 to
  respect the project's <500-line guideline, by moving Security, Ingest
  paths, and Retention/purge into their own pages (consistent with how
  ADRs are already split out).
- **docs/ingest.md (new)** — watch-folder ingest (admin-only, absolute
  `root_path` required, `stalled` semantics) and meeting-bot ingest (mint/
  list/revoke lifecycle, one-time-token warning, no-expiry, stale-session
  sweep at 4h/15min). Verified against `api/watch_folders.py`,
  `api/bot_credentials.py`, `api/bot.py`, `pipeline/bot.py`,
  `bot_credentials.py`, `pipeline/bot_config.py`.
- **docs/retention.md (new)** — full retention/purge semantics: `null` =
  keep forever, `0` = purge now (only negative rejected), full purge vs.
  audio-only purge, the dry-run preview, the daily sweep vs. on-demand
  purge, and that provenance never protects anything from purge. Verified
  against `retention.py`, `pipeline/purge.py`, `api/retention.py`.
- **docs/user-guide.md** — added "Recording live in the browser" section
  (browser capture is member/admin, driven by an actual UI component
  `web/src/components/CaptureRecorder.tsx` on the meetings list page).
- **zensical.toml** — nav updated: new top-level "Security status" entry,
  "Admin guide" turned into a subsection (Overview/Ingest paths/Retention
  and purge) so every new page is reachable from nav, not orphaned.
- **`.superpowers/` move** — `docs/superpowers/phase-1-deferred.md` and
  `docs/superpowers/specs/2026-07-31-meeting-mgr-design.md` (internal
  planning artifacts, not in nav, but zensical was still building and
  publishing them) moved via `git mv` to the repo-root `.superpowers/`
  directory, which already holds other internal planning docs and is not
  under `docs/`. Resolves finding #10.
- Findings #11/#12 (agent docs, `CONTEXT.md`) — verified already current,
  no changes made, per the report's own note.

## Product gap found, not fixed (out of scope — docs/ only)

**There is no first-admin bootstrap mechanism.** Verified directly:
`migrations/versions/0001_initial.py` seeds one `Organization` ("default")
and zero `Account` rows. The only place a new `Account` is ever
constructed outside tests is the OIDC callback
(`src/meeting_mgr/api/auth.py`), which always assigns `role="member"`.
There is no `POST /accounts` endpoint, and local-password/mTLS login can
only *match* an existing Account, never create one. A fresh deployment
with no OIDC configured has no way to log in at all through the product's
own surface; a fresh deployment with OIDC configured gets a first login
but never an admin.

Documented this honestly in the new "Getting started" section of
admin-guide.md as a real gap with a manual-SQL workaround (insert an
Account row directly via `psql`, using `hash_password()` from
`src/meeting_mgr/auth/password.py` for a password-login admin, or promote
an OIDC-auto-provisioned member via `UPDATE account SET role='admin'`).
Recommend filing a GitHub issue for a proper bootstrap/invite flow — I did
not find an existing one (`gh issue list --search "admin bootstrap"` /
`"first admin"` returned nothing relevant).

## Concerns / things worth a second look

- The manual-SQL admin-bootstrap workaround in the getting-started section
  is a genuine operational path (verified `hash_password()`'s exact
  shape), but it's still a workaround, not a supported flow — flagging in
  case the team wants a real bootstrap command before shipping this as the
  documented "first run" story.
- I did not touch anything under `src/`, `tests/`, or `web/` — the
  first-admin gap and the two new security caveats (bot token no-expiry,
  superuser DB creds for auth bootstrap) are documentation of existing
  behavior only, not proposals to change it.
- Commits: everything landed in a single commit (`1e5027c`) rather than
  split per-area, since the `.superpowers/` file moves were already staged
  before I split the admin-guide content into subpages, and re-staging
  separately would have meant an extra reset step. Happy to rewrite as
  three commits (security fix / new-feature docs / superpowers-move) if a
  cleaner history is wanted.
