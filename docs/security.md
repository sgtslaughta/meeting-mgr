# Security status — read this first

For whoever deploys and operates Meeting-MGR. If you want to know what a
user sees, read the [user guide](user-guide.md) instead; for everything
else operational, see the [admin guide](admin-guide.md).

**Authentication, authorization, and tenancy are implemented and shipped on
`main`.** Every request is authenticated (OIDC, local password, or optional
mTLS — see below), every Account has an org-wide Role (`admin`/`member`/
`auditor`), every Meeting has a per-meeting Visibility (`private`/`shared`/
`organization`), and Postgres row-level security enforces tenant isolation
as a database-level backstop, not just an application-layer check. The
single authorization chokepoint (`src/meeting_mgr/authz.py`:
`authorize()`/`require_role()`/`readable_meetings_filter()`) is applied
across every endpoint that touches a Meeting or other tenanted resource.

This replaces an earlier version of this page that described Phase 3 as
"not yet complete" — that was accurate while Phase 3 was in progress and is
no longer true. The caveats below are the real, current ones; there is no
longer a blanket "treat everything as open" warning to make.

## What's still worth knowing

- **Bot bearer tokens never expire.** A meeting-bot credential
  (`POST /bot-credentials`, see [Meeting-bot ingest](ingest.md#meeting-bot-ingest))
  is valid until explicitly revoked — there is no expiry column on
  `BotCredential`. If a token leaks, revoking it
  (`POST /bot-credentials/{id}/revoke`) is the *only* mitigation; treat
  these tokens with the same care as a long-lived API key.
- **The `api` process holds superuser database credentials for identity
  bootstrap.** Login (`POST /auth/login`) and the OIDC callback
  (`GET /auth/oidc/callback`) run before the request's Organization is
  known, so they cannot use the tenant-scoped, RLS-restricted
  `DATABASE_URL_APP` role — they use the untenanted, RLS-bypassing
  `DATABASE_URL` (superuser) connection instead, the same one the `migrate`
  service uses for DDL. This is a deliberate, narrow exception (see the
  "Untenanted, RLS-bypassing session" comments in `src/meeting_mgr/api/
  auth.py` and `src/meeting_mgr/auth/deps.py`), but it means the `api`
  container's environment carries superuser DB credentials, not just the
  least-privilege `meeting_app` role. Tracked as
  [GitHub issue #37](https://github.com/sgtslaughta/meeting-mgr/issues/37).
- **The react-router advisory** (see
  [Known accepted risk](admin-guide.md#known-accepted-risk-react-router-advisory)
  in the admin guide) and **OIDC default-organization auto-provisioning**
  (next section) are both still open and worth reading in full.

## OIDC auto-provisioning and the default organization

`GET /auth/oidc/callback` upserts an Account keyed on `oidc_subject` and
places every newly-provisioned account in the **`default`** Organization
with `role="member"`. A `member` can read every meeting whose visibility is
`organization`.

**This is an operator decision you must consciously accept, not a footnote:**
pointing this deployment at an identity provider means **every user that
IdP will authenticate becomes a member of the default organization** and
gains read access to its organization-visible meetings. For a single-company
self-hosted instance with its own IdP, this is the desired behaviour —
first-time SSO login just works, no invite flow needed. It becomes a
problem the moment the deployment points at a shared or multi-tenant IdP
(e.g. a generic Google Workspace/Okta tenant used by other, unrelated
apps), or once a second Organization is ever added to this instance.

**What to do about it:** only point this deployment at an IdP whose entire
user base should have access to the default organization. Do not wire it
up to an IdP that authenticates people outside that trust boundary.
[GitHub issue #36](https://github.com/sgtslaughta/meeting-mgr/issues/36)
tracks whether to add org-to-claim mapping so a single IdP could serve
multiple Organizations safely; until that lands, one IdP means one trusted
user population.

## Upgrading an existing deployment makes every old meeting private

Phase 3's migration adds `Meeting.visibility` (`NOT NULL`,
`server_default='private'`) and `Meeting.owner_account_id` (nullable).
Every meeting created before Phase 3 therefore becomes **private with no
owner** the moment the migration runs.

Once authorization is enforced, a `private` meeting is readable only by its
owner (there is none, for pre-existing meetings) and by the `admin`/
`auditor` roles. **So after upgrading, ordinary members will not be able to
see any pre-existing meeting** — this is deliberate, a security migration
should fail closed, and defaulting new rows to `organization` visibility
would have silently exposed your entire meeting history instead.

If you're upgrading a running deployment, know the remedy before you run
the migration: an admin can reassign ownership or change visibility on the
pre-existing rows. For example, to make every meeting created before the
upgrade organization-visible:

```sql
UPDATE meeting
SET visibility = 'organization'
WHERE created_at < '2026-07-31'  -- the date you ran the Phase 3 migration
  AND owner_account_id IS NULL;
```

Or to assign them to a specific owner instead:

```sql
UPDATE meeting
SET owner_account_id = <admin-account-id>   -- integer, e.g. 1
WHERE created_at < '2026-07-31'
  AND owner_account_id IS NULL;
```

Run either statement deliberately, after reviewing what those meetings
actually contain — bulk-granting organization-wide visibility to your
entire pre-existing history is exactly the exposure the default was chosen
to avoid.

## Row-level security covers child artifact tables too

Postgres RLS is enabled, with a `tenant_isolation` policy, on every tenanted
table — not just `organization`, `meeting`, `account`, and
`audit_log_entry`, but also the child artifact tables that only carry a
`meeting_id` foreign key: `segment`, `key_topic`, `minute`, `action_item`,
`decision_point`, `speaker_cluster`, `recording`, `meeting_share`, and
`attribution` (the last chained through `speaker_cluster`, which has no
`organization_id` of its own). This closed what was previously tracked as
[GitHub issue #35](https://github.com/sgtslaughta/meeting-mgr/issues/35)
(migration `c4d8e2f1a6b3_rls_child_tables.py`) — that issue is now resolved,
not open.

**What this means in practice:** RLS is the backstop that still protects
transcript segments and derived artifacts from cross-tenant exposure even
if an authorization bug ever slipped past the application-layer chokepoint
— the database itself won't hand rows from another organization's session
to a query that doesn't set `app.org_id` to that organization.
