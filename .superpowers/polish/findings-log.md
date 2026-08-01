# Polish pass findings log

## Pass 2 — simplification (complete)
Verdict: codebase is unusually tight. Most apparent duplication carries a docstring explaining a real invariant (RLS bypass windows, timing-oracle defence, S3 append semantics), so it is intentional rather than accidental. ~40-55 lines removable in total.
- Dead code: 1 high-confidence. bot_credentials.py:37-41 get_bot_credential_by_id has NO production caller — grep-confirmed. auth/bot_deps.py inlines s.get() instead of calling it. Only its own dedicated tests use it. ~25-35 lines including test cleanup. NOTE: those tests were themselves the subject of a Task 2 fix round (the positive-path test was added because only the absent case was covered). Deleting the helper deletes that coverage too, which is correct if the helper goes.
- Duplication: 1, already tracked as issue #38 and its scope CONFIRMED CORRECT — capture.py/bot.py only. watch.py does NOT participate. ~15-20 lines.
- Explicitly NOT recommended: extract.py's 4-function structural similarity. Single shape per caller; consolidating would trade real duplication for a config-driven abstraction with exactly 4 call sites. Correct call.
- Zero findings for: reinvented stdlib, over-defended code, files over 500 lines, speculative abstractions worth cutting.

## Pass 3 — docs and UX (complete)
3 Critical, 6 Important, 3 Minor. Reviewer used a detached worktree and removed it; no files edited, no build run in the main checkout.
- CRITICAL 1: docs/admin-guide.md "Security status" AND docs/index.md's deploy warning are ACTIVELY FALSE, not merely stale. Both still say auth/authz is "Phase 3, not yet complete" and that the RLS child-tables gap (issue #35) is open. Both are wrong on main today: Phase 3 auth is fully implemented (authz.py, auth.py) and migrations/versions/c4d8e2f1a6b3_rls_child_tables.py closed the RLS gap. An operator reading the LANDING PAGE gets a materially wrong risk picture. This is the exact failure mode the dispatch called out — a stale security warning is as harmful as a missing one, because it teaches the reader to distrust the section.
- CRITICAL 2: FOUR SHIPPED FEATURES ARE ENTIRELY UNDOCUMENTED — meeting-bot ingest, watch-folder ingest, browser capture, and retention/purge. Zero mentions in either guide. Retention/purge is the sharpest: admin-only endpoints that IRREVERSIBLY DELETE DATA, with NULL=keep-forever / 0=purge-immediately semantics currently discoverable only by reading source comments. Getting that inverted destroys recordings.
- CRITICAL 3: a new self-hoster cannot reach a first transcribed meeting from the docs. No assembled getting-started sequence, and — now that Phase 3 auth is real — NO DOCUMENTED WAY TO OBTAIN THE FIRST ADMIN LOGIN on a fresh deployment. That is the first concrete blocking step, and it is a direct consequence of auth shipping without the docs catching up.

## Pass 1 — correctness and security (complete)
1 Important, 1 Minor, ZERO Critical. Tenancy/authz/purge/auth discipline held up under review.
- IMPORTANT (reasoned from code, not run as a live race): TOCTOU between chunk upload and finish() in BOTH api/capture.py and api/bot.py. The status check and the manifest-building list_keys() snapshot are not synchronized with the actual object-storage write, so a slow or retried final chunk can be silently excluded from the manifest and orphaned in storage. Silent partial data loss — the recording transcribes, just missing its tail.
- MINOR: api/bot_credentials.py:47 and api/watch_folders.py:62 each hand-roll an owner.organization_id != account.organization_id check that is PROVABLY DEAD TODAY, because RLS on the account table already returns None for a cross-org lookup under get_org_session. This is the exact "second hand-rolled org comparison" anti-pattern authz.py's own docstring calls a defect rather than redundancy. AND — the same confound pattern as everywhere else in this project — no test isolates WHICH guard produces the 422.
- Clean on: authz chokepoint coverage, session-type usage, enumeration-oracle closure in both login and bot-token auth, purge/cascade correctness with sibling-survival tests, the one legitimate SAVEPOINT usage, numeric-vs-lexical chunk ordering. Reviewer specifically noted several places where tests genuinely isolate the mechanism they claim rather than merely agreeing with a stronger guard.

## Fix dispatch plan
Two agents, disjoint directories, one in a worktree because two implementers in one working directory collide on the git index regardless of file overlap.
- CODE agent (main tree): TOCTOU fix, issue #38 shared chunk helper, dead get_bot_credential_by_id removal, and an isolating test for the cross-org owner check. All touch src/ and tests/. The TOCTOU fix and the #38 helper BOTH edit api/capture.py and api/bot.py, so they must be one agent.
- DOCS agent (worktree): docs/ only. 3 Critical + 6 Important + getting-started.

## Docs fix — COMPLETE in worktree agent-ad770c0efecbcf7d3 (1e5027c, bac5ba7), NOT YET MERGED
All 3 Critical + 6 Important + 3 Minor addressed. zensical build --strict passes. Scope verified clean against the worktree's MERGE BASE (980c039): docs/, zensical.toml, .superpowers/ only — zero files under src/, tests/, web/.
NOTE TO SELF — I raised a false alarm here by diffing `main` against the worktree branch. Main had moved forward with the code agent's commits, so those commits appeared as REVERSALS in the worktree's diff and looked like the docs agent deleting src/ files. Always diff a worktree branch against `git merge-base`, never against a moving main.
New pages: docs/security.md, docs/ingest.md, docs/retention.md. admin-guide.md split back under 500 lines. Browser-capture section added to user-guide.md. zensical.toml nav updated.
INCIDENTAL FIND, valuable: the docs agent moved docs/superpowers/* to .superpowers/ because internal planning docs (specs, deferred-findings lists) were being PUBLISHED to the public GitHub Pages site. Nobody asked it to look for that.
PRODUCT GAP FOUND WHILE DOCUMENTING, filed as issue #42: there is NO first-admin bootstrap anywhere in the code. 0001_initial.py seeds an Organization but never an Account; OIDC auto-provisioning always creates role="member"; password and mTLS login only MATCH existing Accounts and never create one. A fresh deployment therefore has no admin and no way to make one, which locks out every admin-only endpoint — retention config, purge, bot credentials, watch folders. Documented with a manual-SQL workaround and filed for a real bootstrap path. This is the kind of gap only writing the getting-started path surfaces: every individual feature works, and the composition is unreachable.

## Code fix — 4 items applied on main (fbea7d4, 980bc18, fbbe7e2, 90c7d2d), 410 tests
## DOCS MERGED to main. 410 green.

## REGRESSION FOUND BY ME IN THE TOCTOU FIX — sent back, fix in flight
The fix introduced a transient Meeting.status = "finishing", committed in its own transaction at api/bot.py:193 (and capture.py:135), with all the manifest work happening AFTER it, outside that transaction. If the process dies in that gap — crash, OOM, eviction, deploy — the Meeting is stranded PERMANENTLY and unreachable by every code path:
  - sweep_stale_bot_sessions (pipeline/bot.py:39,47) selects and rechecks Meeting.status == "capturing" ONLY, so it never touches a "finishing" row.
  - A finish() retry hits the m.status != "capturing" guard at bot.py:174 and 409s forever.
Net effect: the fix traded a narrow SILENT-TRUNCATION window for a PERMANENT STUCK STATE. In the crash case that is strictly worse — before the fix, the same crash left the Meeting in "capturing" and the sweep would eventually fail it out, which is recoverable and visibly failed.
What made it easy to miss: the implementer's own comment at bot.py:175-180 documents this AS A SAFETY PROPERTY — "a legitimately finished Meeting (status now pending, finishing, or failed) can never be raced by the sweep afterwards". That is true and desirable for "pending" and "failed", which are terminal BY DESIGN. For "finishing", which is transient, the identical sentence describes abandonment. Correct reasoning applied to one state too many.
THE GENERAL INVARIANT, now demanded in the fix dispatch: NO CRASH POINT MAY LEAVE A RECORD IN A STATE NOTHING CAN MOVE IT OUT OF. Any new transient status owes an answer to "what reclaims this if the process dies right here". Adding a state to a plain string column costs nothing at the schema level, which is exactly why the question gets skipped.
Also asked: verify api/capture.py for the same defect (identical flip at :135) — browser capture may have NO sweep at all, so its stranded meetings may have no reclaim path whatsoever.
