# Every derived fact carries citations and a Provenance

Meeting-MGR is a system of record whose contents people will dispute, and a
language model cannot be authoritative in one. So every item in a Meeting Record
— Key Topic, Minute, Action Item, Decision Point, and every Attribution — stores
the Segment IDs it was derived from and a Provenance of `inferred`, `confirmed`,
or `unknown`, both of which are shown wherever the item is shown. The model
proposes; a human confirms; a reader can always tell which happened.

## Consequences

- The model is a proposer, never an oracle. Nothing it emits is presented as
  established fact.
- Artifacts are extracted in four separate LLM passes rather than one, so a bad
  artifact can be regenerated alone without discarding human edits to the others.
  This costs more tokens by design.
- Every artifact table carries `citations` and `provenance` columns. Retrofitting
  these later would mean re-processing the archive, since the citation link
  cannot be reconstructed after the fact.
- Purging raw audio under a retention policy breaks click-to-hear-the-quote but
  leaves transcript citations intact — a coherent state the data model must
  represent.
