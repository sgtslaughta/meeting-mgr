import uuid

from meeting_mgr.db import get_session
from meeting_mgr.models import Organization
from meeting_mgr.retention import upsert_policy


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def test_sweep_retention_dispatches_only_organizations_with_a_policy(monkeypatch):
    from meeting_mgr.pipeline import purge as purge_mod

    org_with_policy = _org()
    org_without_policy = _org()
    with get_session() as s:
        upsert_policy(s, org_with_policy, audio_retention_days=30, meeting_retention_days=None)

    dispatched = []
    monkeypatch.setattr(
        purge_mod.purge_organization, "delay", lambda org_id: dispatched.append(org_id)
    )

    purge_mod.sweep_retention()

    assert org_with_policy in dispatched
    assert org_without_policy not in dispatched


def test_sweep_retention_dispatches_an_org_with_only_meeting_retention_set(monkeypatch):
    """NOTE: the task brief's reference test asserts `dispatched == [org_id]`.
    That cannot pass in this suite: Postgres is persistent with no cleanup
    fixture (see conftest.py / global constraints), and several other test
    modules (test_purge_organization.py, test_purge_audio.py,
    test_retention_candidates.py, test_purge_reconciliation.py) commit real
    non-null retention_policy rows for other organizations that stick around
    for the rest of the suite's life -- sweep_retention correctly dispatches
    those too. Asserting exact list equality here is a genuine defect in the
    brief, not a corner we're allowed to cut; membership/count on this
    test's own org_id is the precise assertion the environment allows."""
    from meeting_mgr.pipeline import purge as purge_mod

    org_id = _org()
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=90)

    dispatched = []
    monkeypatch.setattr(purge_mod.purge_organization, "delay", lambda oid: dispatched.append(oid))

    purge_mod.sweep_retention()

    assert dispatched.count(org_id) == 1


def test_sweep_retention_does_not_dispatch_an_all_null_policy_row(monkeypatch):
    """A policy row explicitly reset back to keep-forever (both fields None)
    must not be swept -- upsert_policy() never deletes the row, so the
    dispatch query must filter on the values, not merely row existence."""
    from meeting_mgr.pipeline import purge as purge_mod

    org_id = _org()
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=None)

    dispatched = []
    monkeypatch.setattr(purge_mod.purge_organization, "delay", lambda oid: dispatched.append(oid))

    purge_mod.sweep_retention()

    assert org_id not in dispatched


def test_sweep_retention_dispatches_via_delay_not_inline_call(monkeypatch):
    """Kill: an implementation that calls purge_organization(org_id) directly
    (bypassing Celery) instead of purge_organization.delay(org_id) would
    never populate `delay_calls` below, because we only patched .delay --
    task_always_eager (tests/conftest.py) makes .delay() run the task body
    synchronously in-process, which is exactly why asserting on the
    downstream effect can't tell inline-call and .delay() apart; spying on
    the .delay attribute itself is the only thing that distinguishes them."""
    from meeting_mgr.pipeline import purge as purge_mod

    org_id = _org()
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=30, meeting_retention_days=None)

    delay_calls = []
    monkeypatch.setattr(purge_mod.purge_organization, "delay", lambda oid: delay_calls.append(oid))

    purge_mod.sweep_retention()

    assert delay_calls.count(org_id) == 1


def test_sweep_retention_one_organization_failing_to_dispatch_does_not_block_the_others(
    monkeypatch, caplog
):
    """Kill: an implementation with no try/except around .delay() in the
    dispatch loop -- i.e. following the brief's Step 3 reference code
    verbatim -- would let one organization's dispatch failure raise out of
    sweep_retention and abort the whole nightly sweep, silently leaving
    every organization after the failing one un-dispatched."""
    from meeting_mgr.pipeline import purge as purge_mod

    org_fails = _org()
    org_ok = _org()
    with get_session() as s:
        upsert_policy(s, org_fails, audio_retention_days=30, meeting_retention_days=None)
        upsert_policy(s, org_ok, audio_retention_days=30, meeting_retention_days=None)

    dispatched = []

    def fake_delay(org_id):
        if org_id == org_fails:
            raise RuntimeError("broker unreachable")
        dispatched.append(org_id)

    monkeypatch.setattr(purge_mod.purge_organization, "delay", fake_delay)

    with caplog.at_level("ERROR"):
        purge_mod.sweep_retention()

    assert org_ok in dispatched
    assert org_fails not in dispatched
    assert any(str(org_fails) in record.getMessage() for record in caplog.records)
