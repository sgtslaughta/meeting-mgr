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
- **[Admin guide](admin-guide.md)** — you're standing this up: docker-compose,
  environment variables, the inference endpoint, the diarizer, storage,
  migrations, backup, and — importantly — what security is and isn't in place
  today.
- **[Architecture Decision Records](adr/0001-inference-over-http.md)** — why
  the system is shaped the way it is.
- **[Domain glossary](https://github.com/sgtslaughta/meeting-mgr/blob/main/CONTEXT.md)**
  (`CONTEXT.md`) — the vocabulary this documentation uses throughout
  (Participant vs. Account, Provenance, Speaker Cluster, and the rest).

## Read this before you deploy it anywhere reachable

Meeting-MGR has **no authentication or authorization yet**. Any caller that
can reach the API can read and change any meeting. Organizations, Accounts,
Roles and the Audit Log are tracked as
[Phase 3](https://github.com/sgtslaughta/meeting-mgr/issues/30) and not built.
See the admin guide's [Security status](admin-guide.md#security-status-read-this-first)
section before you put this on any network you don't fully trust.
