# User guide

This is for someone who attended a meeting and wants the record of it — not
for whoever installed Meeting-MGR. If you're looking for docker-compose and
environment variables, see the [admin guide](admin-guide.md) instead.

## Uploading a recording

On the meetings list (the site's home page) there's a small form: a title
field and a file picker that accepts audio or video. Fill in a title, choose
the file, and submit. The upload streams straight to storage — there's no
practical size limit imposed by the app itself, so a two-hour video works the
same as a five-minute clip.

Once the upload finishes, the meeting appears in the list with status
`pending`, and processing starts automatically. You don't trigger anything
else — no "start processing" button.

## What the processing stages mean

A recording moves through a fixed pipeline. In order:

| Stage | What happens |
| --- | --- |
| `normalize` | The audio/video is converted to a standard 16 kHz mono WAV. This is what makes the later stages consistent regardless of what format you uploaded. |
| `diarize` | The audio is split into **Speaker Clusters** — groups of speech judged to come from the same voice. At this point a cluster has no name, just a label like `SPEAKER_00`. |
| `transcribe` | Speech becomes text, as a sequence of **Segments** (a segment is a short span of speech with a start time, an end time, and text). |
| `align` | Each Segment is matched to the Speaker Cluster that spoke it. |
| `attribute` | A model reads the transcript for name cues ("Thanks, Sarah") and proposes a real name for each cluster. These proposals are always `inferred` — nobody has confirmed them yet. |
| `key_topics` / `minutes` / `action_items` / `decision_points` | Four separate passes over the transcript, each producing one kind of derived fact. Each is optional in the sense that one failing doesn't stop the others. |
| `publish` | The meeting becomes visible with everything gathered so far. |

### Following progress

The meeting detail page shows the current stage (e.g. "transcribe…") while
processing is underway, and updates live — you don't need to refresh the
page. If a stage fails, the page shows which one, and everything the pipeline
managed to produce before the failure is still there and still usable. A
meeting with a transcript but no action items is a normal, working state, not
a broken one — a failed extraction stage doesn't take anything else down with
it.

## Reviewing speakers

Under "Speakers" on the meeting page, each detected Speaker Cluster gets one
row: its label, a short audio sample you can play (the longest single span of
speech from that cluster, so you get a real listen, not a fragment), a text
box, a provenance badge, and a **Confirm** button.

- **Listen to the sample** to hear who it is.
- **Type a name and press Confirm** to attribute that cluster to that person.
  This sets provenance to `confirmed` — a human decided, not a model guess.
- **If the attribution proposed a name already** (from the `attribute` stage),
  it's pre-filled in the box, but it's still `inferred` until you press
  Confirm. Editing the name and confirming corrects it and marks it
  `confirmed` in the same action.
- **"I don't know who this is"** — leave the box empty and press Confirm.
  This does *not* create a placeholder or an "unknown" fact; it removes any
  attribution for that cluster entirely. The absence of an attribution *is*
  the honest state: nobody has decided. The cluster shows as `UNKNOWN` in the
  transcript until someone does attribute it.

## Reading the transcript

The transcript is the full text of the meeting, one line per Segment, each
line labeled with the speaker — either the confirmed/proposed name, or the
cluster's raw label, or `UNKNOWN` if no attribution exists at all.

## Reviewing derived artifacts

Below the speakers, four sections list what the model extracted: **Key
Topics**, **Minutes**, **Action Items**, and **Decision Points**. Each item
shows its text (or title), a provenance badge, and the citation numbers it's
backed by.

- **Editing an item**: click into its text and change it; the edit saves when
  you click away. Editing an item sets its provenance to `confirmed` — you
  looked at it and it's now correct as written, whoever wrote it first.
- **Deleting an item**: removes it permanently. There's no undo — if the
  model was simply wrong, delete it; if you want it back later, regenerate
  the section (see below).
- **Regenerating a whole section**: the "Regenerate ⟨section⟩" button
  discards every item currently in that section — including any edits you've
  made — and reruns that one extraction pass from scratch. You're asked to
  confirm first, because this is destructive. Regenerating one section never
  touches the other three; your edits to Minutes survive a Key Topics
  regeneration. Regeneration runs in the background and can take a while
  against a real LLM; the page shows "Regenerating…" and polls for the result.

### What "no evidence linked" means

If an item shows "no evidence linked" instead of citation numbers, something
is off — every fact the model returns is supposed to cite real transcript
segments, and facts that fail that check are dropped before you ever see
them, so this case should be rare. Treat it as reason to double-check the
item.

## Provenance badges — what they mean and why they matter

Every derived fact — every Attribution, every Key Topic, Minute, Action Item
and Decision Point — carries a small badge:

| Badge | Meaning |
| --- | --- |
| `inferred` | Proposed by a model. Nobody has checked this yet. Treat it as a guess, not a fact. |
| `confirmed` | Confirmed by a person — either it was reviewed and kept, or someone entered/edited it directly. |
| `unknown` | Undecided. (Shown for attributions with no confirmed or inferred link yet.) |

This is the whole point of reviewing a meeting here rather than trusting a
raw AI summary: **you can always tell, at a glance, whether something is a
machine's guess or a human's decision.** An Action Item assigned to the wrong
person is a real failure mode for this kind of tool — that's exactly the kind
of mistake the badge exists to surface before it becomes "the record."

## Following a citation to its evidence

Every citation on a derived fact is a clickable segment number. Clicking one:

- scrolls the transcript to that segment and highlights it, and
- seeks the audio player to that segment's start time and starts playing it.

This is how you check a claim instead of taking it on faith: click the
citation, hear (or read) the exact moment it came from.
