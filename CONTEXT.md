# Meeting-MGR

A self-hosted meeting records keeper. Recordings go in; structured, attributed
meeting records come out.

## Language

### Transcription

**Transcript**:
The full text of a Meeting as an ordered list of Segments.
_Avoid_: text, captions, notes

**Segment**:
A contiguous span of speech with a start time, an end time, text, and the
Speaker Cluster it belongs to. The atom everything else cites.
_Avoid_: chunk, utterance, line

**Inference Endpoint**:
An OpenAI-compatible HTTP service the app calls for language or transcription
work. Configured, never bundled — the app ships no models and assumes no GPU.
_Avoid_: provider, model, backend, engine

### Meeting Record

**Meeting Record**:
Everything derived from a Transcript: Key Topics, Minutes, Action Items and
Decision Points. Every item in it cites the Segments it came from and carries a
Provenance.
_Avoid_: summary, output, results, analysis

**Key Topic**:
A subject the Meeting spent meaningful time on.
_Avoid_: theme, subject, tag

**Minute**:
A single narrative entry recording what was said or resolved at one point in the
Meeting.
_Avoid_: note, summary line, item

**Action Item**:
Something a Participant committed to doing, with an optional due date and a
status. The one artifact where wrong Attribution is a real failure.
_Avoid_: task, todo, follow-up, assignment

**Decision Point**:
A place where the Meeting either resolved something or failed to. Carries whether
it was settled or left contested, and who held which position.
_Avoid_: decision, issue, disagreement, blocker

### People

**Participant**:
Someone who spoke in a Meeting. Identified within the recording, not by a login.
_Avoid_: user, attendee, speaker

**Account**:
Someone who logs into Meeting-MGR. May be linked to a Participant, but is a
separate entity — most Participants never have one.
_Avoid_: user, member

**Organization**:
The tenant boundary. Every Meeting and Account belongs to exactly one; nothing
is ever visible across Organizations.
_Avoid_: tenant, workspace, team, company

**Role**:
An Account's standing within its Organization: `admin`, `member`, or `auditor`.
An auditor may read everything in the Organization, including the Audit Log, and
may change nothing.
_Avoid_: permission, group, tier, access level

**Visibility**:
Who may see a Meeting: `private` (owner and explicit shares only), `shared`
(named Accounts), or `organization` (everyone in the Organization). Independent
of Role — an auditor's read access is not a Visibility grant.
_Avoid_: permission, sharing, privacy, scope

**Audit Log**:
An append-only record of who did what and when. Never edited, never deleted by
any Role.
_Avoid_: history, activity feed, event log

### Meetings

**Meeting**:
A single recorded gathering and everything derived from it.
_Avoid_: session, call, event

**Recording**:
The raw captured audio or video for a Meeting, plus the normalized audio derived
from it. A Meeting has exactly one.
_Avoid_: file, upload, media, asset

**Ingest Adapter**:
A way a Recording arrives — upload, watch folder, browser capture, or meeting
bot. Every adapter hands the pipeline the same thing, so they differ only in how
they obtain the Recording.
_Avoid_: source, importer, connector

**Speaker Cluster**:
A set of speech segments that diarization judged to come from one voice. Has no
name of its own — it becomes meaningful only once attributed to a Participant.
_Avoid_: speaker, voice, diarization label

**Attribution**:
The link from a Speaker Cluster to a Participant, together with how that link
was decided. Every Attribution carries a Provenance.
_Avoid_: assignment, mapping, identification

**Provenance**:
How a fact came to be known: `inferred` (a model proposed it), `confirmed` (a
human accepted or corrected it), or `unknown` (undecided). Displayed wherever
the fact is displayed — a reader can always tell a guess from a decision.
_Avoid_: source, confidence, origin
