# Meeting-MGR — Design Spec

**Date:** 2026-07-31
**Status:** Approved
**Source:** `plans/project_vision.md`, sharpened through a 12-question grilling session.

Terminology in this document is defined in
[`CONTEXT.md`](https://github.com/sgtslaughta/meeting-mgr/blob/main/CONTEXT.md).
Capitalised terms are glossary terms and are used exactly as defined there.

---

## 1. Purpose

A self-hosted meeting records keeper. A Recording goes in; a structured,
attributed, citable Meeting Record comes out — Key Topics, Minutes, Action Items
and Decision Points, each traceable to the Segments it came from and to the
Participant who said it.

The product is a **system of record**. Its contents will be disputed by the
people described in them, which drives two non-negotiable properties: every
derived fact is citable, and every derived fact is visibly marked as a machine
guess or a human decision.

## 2. Scope

### In scope for v1

- Four Ingest Adapters: file upload + API, watch folder, browser capture, meeting bot
- Diarization, transcription, alignment, attribution
- Four extracted artifact types, each structured and citing Segments
- Review UI for confirming and correcting Attributions and artifacts
- Organizations, Accounts, Roles (`admin` / `member` / `auditor`)
- Per-Meeting Visibility (`private` / `shared` / `organization`)
- Authentication: OIDC, local password, optional mTLS subject extraction
- Append-only Audit Log
- Configurable retention with hard delete and tombstones

### Explicitly out of scope for v1

These were considered and deferred; do not build them without a new decision.

- Full RBAC (custom roles, groups, granular permissions) — the two-axis model
  composes into it later without a migration
- Multi-region deployment
- Horizontal GPU worker pool — a single diarization worker is v1's ceiling
- Automatic voiceprint matching — embeddings are stored so this becomes a feature
  toggle later, not a re-processing job
- Bundled inference models of any kind

### Decomposition note

This spec is too large for a single implementation plan. It decomposes along the
build order in §10; **each phase gets its own plan**. Phase 1 is the first plan
to write.

---

## 3. Architecture

Four deployable units plus operator-provided inference endpoints:

| Unit | Responsibility | Ships with GPU dep? |
| --- | --- | --- |
| **API** (FastAPI) | HTTP, auth, authorization, orchestration, job dispatch | No |
| **Celery workers** | Pipeline stages, retries, retention purge | No |
| **Diarization worker** | `POST /diarize` → Speaker Clusters + embeddings | Yes — the only one |
| **Web** (React + Vite + TS) | Upload, review, edit, search | No |

Backing services: **Postgres** (system of record), **Redis** (Celery broker),
**S3-compatible object storage** (Recordings; MinIO in the default compose stack).

Inference is HTTP — see [ADR-0001](../../adr/0001-inference-over-http.md). The
API and Celery images carry no `torch` and assume no GPU. Diarization is the
single exception, isolated in its own container — see
[ADR-0002](../../adr/0002-diarization-as-owned-service.md).

### Ingest Adapters

All four adapters terminate at the same operation: *create Meeting, store
Recording, enqueue pipeline*. Nothing downstream can tell which adapter ran.
This is what makes the meeting bot additive scope rather than entangled scope,
and why it is built last.

---

## 4. Pipeline

Seven Celery stages. Each checkpoints its result in Postgres and is
independently retryable.

| # | Stage | Input | Output |
| --- | --- | --- | --- |
| 1 | Normalize | Recording | 16 kHz mono WAV (ffmpeg) |
| 2 | Diarize | normalized audio | Speaker Clusters + voice embeddings |
| 3 | Transcribe | normalized audio | Segments (ASR endpoint) |
| 4 | Align | Segments + Clusters | Segments with `cluster_id` set |
| 5 | Attribute | aligned Transcript | Attributions, Provenance `inferred` |
| 6 | Extract ×4 | aligned Transcript | Key Topics, Minutes, Action Items, Decision Points |
| 7 | Publish | all of the above | Meeting visible; owner notified to confirm |

**Stage 5** asks the LLM to propose Participant names from in-transcript cues
("Thanks, Sarah"). Proposals are always `inferred` and always presented as
proposals; a human confirms or corrects them in the review UI, which promotes
them to `confirmed`.

**Stage 6** is four separate LLM calls, not one — see
[ADR-0003](../../adr/0003-provenance-and-citations.md). Each returns structured
JSON in which every item carries the Segment IDs it derived from.

**Failure** at any stage records the failed stage on the Meeting and leaves it in
a named failed state. Re-running resumes from that stage; earlier stages are not
recomputed.

---

## 5. Data model

Core entities and their relationships:

```
Organization
├── Account (role: admin | member | auditor)
└── Meeting (owner, visibility: private | shared | organization)
    ├── MeetingShare → Account
    ├── Recording (raw object key, normalized object key)
    ├── Segment (start, end, text, cluster_id)
    ├── SpeakerCluster (embedding)
    ├── Attribution (cluster → Participant, provenance)
    ├── KeyTopic       ┐
    ├── Minute         │ each: citations[] of Segment IDs
    ├── ActionItem     │       + provenance
    └── DecisionPoint  ┘
Participant  (org-scoped; not an Account)
AuditLogEntry (append-only)
```

### Two invariants that hold everywhere

1. **Every derived fact carries `citations` and `provenance`.** No exceptions.
   A human edit sets `provenance = 'confirmed'`.
2. **Every Organization-scoped query filters by `organization_id`**, enforced by
   Postgres row-level security rather than developer discipline. RLS is the
   control; application-level filtering is defence in depth, not the primary
   mechanism.

### Artifact specifics

- **ActionItem** — Participant, optional due date, status. This is the artifact
  where wrong Attribution is a genuine failure, so its Attribution provenance is
  surfaced prominently in the UI.
- **DecisionPoint** — carries whether the point was *settled* or left
  *contested*, and which Participant held which position.

---

## 6. Authentication and authorization

### Three entry paths, one Account

| Path | Mechanism |
| --- | --- |
| OIDC | `authlib` relying party. Meeting-MGR is never an identity provider. |
| Local password | Email + password, for instances with no IdP. |
| mTLS | Optional. Client-certificate subject extracted to identify the Account. |

### mTLS trust boundary — security-critical

When mTLS terminates at a reverse proxy, the app reads identity from a forwarded
header. That header **must** be accepted only from an explicit allowlist of proxy
source IPs, and **must** be unconditionally stripped from any request arriving
from any other source.

Without this control, any client able to reach the API directly can authenticate
as any user by setting a single header. This is a required control with a
required test, not a configuration recommendation.

### Two independent authorization axes

- **Role** — org-wide standing. `auditor` may read everything in the
  Organization including the Audit Log, and may write nothing.
- **Visibility** — per-Meeting reach, controlled by the Meeting's owner.

An auditor's read access is **not** a Visibility grant; the two axes are
evaluated independently. Both are evaluated in a **single chokepoint function**,
so there is exactly one place in the codebase where authorization can be wrong.

### Audit Log

Append-only. No Role can edit or delete entries. Records actor, action, target,
and timestamp. Deletions record a tombstone — who deleted what and when — but
never retain deleted content.

---

## 7. Retention and deletion

Per-Organization configuration:

- Keep everything forever (default)
- Optionally purge raw audio after N days, retaining the Transcript and all
  derived artifacts
- Optionally purge the entire Meeting after N days

Deletion is a **hard delete** of both database rows and object storage keys,
leaving an Audit Log tombstone. This is what allows a GDPR erasure request to be
satisfied; soft deletion cannot.

Purging raw audio while retaining the Transcript is an explicitly supported
state: citations still resolve to text, but click-to-hear-the-quote is
unavailable. The UI indicates this rather than presenting a broken control.

Voice embeddings are biometric data under GDPR Art. 9 and are purged with the
Meeting.

---

## 8. Error handling

- **Inference endpoints are untrusted.** They time out, rate-limit, and return
  malformed JSON. Every structured call validates against a Pydantic schema and
  retries with bounded exponential backoff. Persistent failure fails only that
  stage.
- **Broker durability is explicit configuration.** Redis is not durable by
  default; a queued job may represent an hour of GPU work on a file uploaded
  once. Durability settings are set deliberately and documented, not inherited.
- **Partial success is a first-class state.** A Meeting with a Transcript but no
  Action Items is useful and must render correctly, with the failed stage visible
  and individually re-runnable.
- **ffmpeg failures are user-facing.** A corrupt or unsupported upload produces a
  clear message naming the problem, not a generic pipeline failure.

---

## 9. Testing

Integration-first, against real Postgres, Redis and MinIO in containers.

Inference endpoints are stubbed with a **fake OpenAI-compatible server** — cheap,
deterministic, and it exercises the same HTTP path as production, including the
timeout and malformed-response branches.

Non-negotiable coverage:

1. The authorization chokepoint — every Role × Visibility combination
2. Tenant isolation — no query crosses an Organization boundary
3. mTLS header spoofing — a forged identity header from a non-allowlisted source
   is rejected
4. Pipeline resume — a failure at each stage resumes correctly without
   recomputing earlier stages
5. Retention — hard delete removes rows and objects, and leaves a tombstone

---

## 10. Build order

Each phase is a separate implementation plan.

| Phase | Contents |
| --- | --- |
| **1** | Core pipeline, upload adapter only, no auth. Recording → Meeting Record. |
| **2** | Review and edit UI — confirm Attributions, correct artifacts, provenance display. |
| **3** | Organizations, Accounts, Roles, Visibility, all three auth paths, Audit Log. |
| **4** | Retention configuration and purge jobs. |
| **5** | Watch folder and browser capture adapters. |
| **6** | Meeting bot adapter. |

Phases 5 and 6 are additive: nothing in phases 1–4 depends on them. Phase 6 is
the largest single line item in the project and is deliberately last.
