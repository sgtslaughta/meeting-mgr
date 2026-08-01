import logging
import uuid
from datetime import datetime, timedelta

from meeting_mgr.bot_credentials import create_bot_credential
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, BotSession, Meeting, Organization
from meeting_mgr.pipeline.bot import sweep_stale_bot_sessions
from meeting_mgr.pipeline.bot_config import STALE_SESSION_SECONDS


def _org_account() -> tuple[int, int]:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        a = Account(organization_id=o.id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return o.id, a.id


def _meeting(org_id, account_id, *, status="capturing") -> int:
    with get_session() as s:
        m = Meeting(organization_id=org_id, owner_account_id=account_id, title="t", status=status)
        s.add(m)
        s.flush()
        return m.id


def _bot_session(org_id, account_id, meeting_id, *, last_activity_at) -> None:
    with get_session() as s:
        cred, _ = create_bot_credential(
            s, org_id, label=f"bot-{uuid.uuid4()}", owner_account_id=account_id
        )
        s.add(
            BotSession(
                organization_id=org_id,
                bot_credential_id=cred.id,
                meeting_id=meeting_id,
                platform_meeting_id=str(uuid.uuid4()),
                last_activity_at=last_activity_at,
            )
        )


def test_a_stale_session_is_marked_failed():
    org_id, account_id = _org_account()
    meeting_id = _meeting(org_id, account_id)
    stale = datetime.utcnow() - timedelta(seconds=STALE_SESSION_SECONDS + 60)
    _bot_session(org_id, account_id, meeting_id, last_activity_at=stale)

    sweep_stale_bot_sessions.run()

    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.status == "failed"
        assert m.failed_stage == "bot_ingest"


def test_a_fresh_session_is_left_alone():
    org_id, account_id = _org_account()
    meeting_id = _meeting(org_id, account_id)
    _bot_session(org_id, account_id, meeting_id, last_activity_at=datetime.utcnow())

    sweep_stale_bot_sessions.run()

    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.status == "capturing"


def test_a_capturing_meeting_with_no_bot_session_is_left_alone():
    """A browser-capture Meeting (api/capture.py) also uses status="capturing"
    but has no BotSession row -- the sweep must not touch it."""
    org_id, account_id = _org_account()
    meeting_id = _meeting(org_id, account_id)

    sweep_stale_bot_sessions.run()

    with get_session() as s:
        assert s.get(Meeting, meeting_id).status == "capturing"


def test_a_session_already_in_a_terminal_state_is_not_re_failed():
    """A Meeting that already finished (or already failed for some other
    reason) before its stale BotSession is swept must not be touched --
    the sweep only ever acts on status == "capturing"."""
    org_id, account_id = _org_account()
    meeting_id = _meeting(org_id, account_id, status="completed")
    stale = datetime.utcnow() - timedelta(seconds=STALE_SESSION_SECONDS + 60)
    _bot_session(org_id, account_id, meeting_id, last_activity_at=stale)

    sweep_stale_bot_sessions.run()

    with get_session() as s:
        m = s.get(Meeting, meeting_id)
        assert m.status == "completed"
        assert m.failed_stage is None


def test_sweep_spans_multiple_organizations():
    """A sweep that only ever handled one tenant's rows would pass a
    single-org test while leaving every other tenant's sessions stuck
    forever -- prove it acts across organizations in one run."""
    org_a, account_a = _org_account()
    org_b, account_b = _org_account()
    meeting_a = _meeting(org_a, account_a)
    meeting_b = _meeting(org_b, account_b)
    stale = datetime.utcnow() - timedelta(seconds=STALE_SESSION_SECONDS + 60)
    _bot_session(org_a, account_a, meeting_a, last_activity_at=stale)
    _bot_session(org_b, account_b, meeting_b, last_activity_at=stale)

    sweep_stale_bot_sessions.run()

    with get_session() as s:
        assert s.get(Meeting, meeting_a).status == "failed"
        assert s.get(Meeting, meeting_b).status == "failed"


def test_one_failed_row_does_not_block_the_others(monkeypatch, caplog):
    """Kill-test proof for the per-row try/except: force the write for one
    organization's session to raise mid-sweep, and confirm the later
    organization's session is still swept -- same isolation contract as
    sweep_retention/scan_watch_folder."""
    org_bad, account_bad = _org_account()
    org_good, account_good = _org_account()
    meeting_bad = _meeting(org_bad, account_bad)
    meeting_good = _meeting(org_good, account_good)
    stale = datetime.utcnow() - timedelta(seconds=STALE_SESSION_SECONDS + 60)
    _bot_session(org_bad, account_bad, meeting_bad, last_activity_at=stale)
    _bot_session(org_good, account_good, meeting_good, last_activity_at=stale)

    from meeting_mgr.pipeline import bot as bot_module

    real_record_audit = bot_module.record_audit

    def _flaky_record_audit(s, *, organization_id, **kwargs):
        if organization_id == org_bad:
            raise RuntimeError("simulated failure")
        return real_record_audit(s, organization_id=organization_id, **kwargs)

    monkeypatch.setattr(bot_module, "record_audit", _flaky_record_audit)

    with caplog.at_level(logging.ERROR, logger="meeting_mgr.pipeline.bot"):
        sweep_stale_bot_sessions.run()

    with get_session() as s:
        assert s.get(Meeting, meeting_bad).status == "capturing"
        assert s.get(Meeting, meeting_good).status == "failed"
    assert any(str(meeting_bad) in r.message for r in caplog.records)
