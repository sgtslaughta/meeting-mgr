# Meeting-MGR

A self-hosted meeting records keeper. Give it a recording; it gives you a
citable, attributed Meeting Record — Key Topics, Minutes, Action Items and
Decision Points, each traceable to the moment in the transcript it came from
and to the person who said it.

It runs on your own infrastructure. It never bundles a model — you point it at
an inference endpoint you already run or pay for, and it calls that endpoint
over HTTP for both transcription and language work.

## The idea the whole product rests on

Meeting-MGR extracts facts with a language model, and a language model is a
proposer, not an oracle. So every fact it derives carries two things wherever
it is shown:

- **Provenance** — `inferred` (a model guessed), `confirmed` (a human decided),
  or `unknown` (nobody has decided yet).
- **Citations** — the exact transcript segments the fact was derived from. A
  fact the model produced with no valid citation is dropped before it ever
  reaches you; it is never shown as an orphaned claim.

Read [ADR-0003](adr/0003-provenance-and-citations.md) for why this is
non-negotiable in the design.

## Where to go

- **[User guide](user-guide.md)** — you attend meetings and want to upload a
  recording, review who said what, and read the resulting minutes.
- **[Admin guide](admin-guide.md)** — you're standing this up: getting
  started, docker-compose, environment variables, ingest paths, retention
  and purge, the inference endpoint, the diarizer, storage, migrations, and
  backup.
- **[Security status](security.md)** — authentication, authorization,
  tenancy, and what's still worth knowing.
- **[Architecture Decision Records](adr/0001-inference-over-http.md)** — why
  the system is shaped the way it is.
- **[Domain glossary](https://github.com/sgtslaughta/meeting-mgr/blob/main/CONTEXT.md)**
  (`CONTEXT.md`) — the vocabulary this documentation uses throughout
  (Participant vs. Account, Provenance, Speaker Cluster, and the rest).

## Security, before you deploy it anywhere reachable

Meeting-MGR authenticates every request (OIDC, local password, or optional
mTLS), enforces org-wide Roles (`admin`/`member`/`auditor`) and per-meeting
Visibility, and backs tenant isolation with Postgres row-level security —
not just an application-layer check. A handful of caveats remain (an
unexpiring bot bearer token, OIDC auto-provisioning into a single default
organization, a known dependency advisory). Read the
[Security status](security.md) page for the full, current picture before
you put this on any network you don't fully trust.
