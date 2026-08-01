# fix-test-coverage — status (in progress, blocked on infra)

Test stack (Postgres 55432 / Redis 56379 / MinIO 59000) went down mid-session
(connection refused on all three). `docker ps` shows only dev-stack
containers (minio on 9000-9001, postgres/redis with no published ports) —
not the test-stack port mapping. I have not run `docker compose` myself.
`git diff --stat -- src/` in this worktree is clean — all mutation-testing
edits to src/ were reverted. Waiting for the stack to come back before
finishing #9's attribute.py mutation and the two clean full-suite runs.

## #8 — align.py multi-span / empty-spans (DONE, mutation-verified)

Added to tests/test_align.py:
- `test_align_sums_overlap_across_disjoint_spans` — cluster A has two spans
  that individually overlap less than cluster B's one span, but summed
  beat it. Kill line: `total = sum(...)` in align.py. Mutated to
  `max(..., default=0.0)` → this test went red (423 vs 424), all other
  align tests stayed green.
- `test_align_never_assigns_a_cluster_with_empty_spans` — an empty-spans
  cluster alongside a real match must never win, including as a
  zero-overlap default. Kill line: `if total > best:` in align.py. Mutated
  to `>=` → this test went red, plus 2 pre-existing tests also went red
  (corroborating, not vacuous).

## #9 — participant dedup (partially done)

Added to tests/test_attribute.py:
- `test_attribute_dedups_two_clusters_resolving_to_the_same_name` — two
  SpeakerClusters in the SAME (default) org both proposed as "Sarah" via
  attribute() must produce 2 Attributions pointing to 1 Participant.
- `test_resolve_participant_dedup_is_scoped_per_organization` — same name
  in a different org must resolve to a different Participant id.

Mutation results:
- Removed `organization_id=org_id` from the filter_by in
  `resolve_participant` (src/meeting_mgr/participants.py) → the cross-org
  test went red (crashed with MultipleResultsFound, correctly proving
  isolation was broken). CONFIRMED KILLED.
- Removed the pre-check (`p = s.query(...).one_or_none(); if p is not
  None: return p.id`) entirely, forcing every call through the insert path
  → the same-org dedup test STAYED GREEN. Root cause: Participant has
  `UniqueConstraint(organization_id, name)` and `resolve_participant`'s
  except-IntegrityError branch re-queries and returns the existing row.
  With both clusters in one session/transaction, that backstop alone
  reproduces the exact same outcome (1 participant, 2 attributions) even
  with the primary lookup deleted. **This is a real finding, not a bug in
  my test**: the same-org dedup test is genuinely correct behavior-pinning
  (attribute() truly does dedup two clusters to one Participant, which was
  previously untested) but it cannot be proven to hinge on
  `resolve_participant`'s own pre-check via mutation, because a second,
  independent mechanism (DB unique constraint + exception recovery)
  produces the identical observable result. I was mid-way through testing
  a mutation directly in `attribute.py` (bypass `resolve_participant`
  entirely, insert a fresh `Participant` per cluster) when the DB
  connection dropped — that mutation is a more promising kill candidate
  since it removes both defenses' lookup path at once and should surface
  a genuine 2-participant outcome or an IntegrityError crash. Need infra
  back to finish this and report the actual split.

## #10 — end-to-end provenance (test written, not yet mutation-tested)

Extended `test_upload_to_published_record` in tests/test_end_to_end.py to
assert `citations == [seg_id]` and `provenance == "inferred"` for
`key_topics`, `minutes`, and `decision_points` (previously only checked on
`action_items`). All four derived-record types share the same `_Derived`
base (provenance/citations columns), so this closes the coverage gap
cleanly. Not yet run against a mutation (e.g. flipping a default
provenance value in `models/record.py`, or removing a field from the
response serializer) — will do once infra is back.

## Commits

None yet — holding until #9's mutation-testing is settled and both full
clean runs (green twice against the same DB) pass, per instructions.
