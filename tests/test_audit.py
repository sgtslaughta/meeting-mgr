from meeting_mgr.audit import record_audit
from meeting_mgr.db import get_session
from meeting_mgr.models import Organization


def test_record_audit_appends_a_row():
    with get_session() as s:
        org_id = s.query(Organization).filter_by(name="default").one().id
        entry = record_audit(
            s,
            organization_id=org_id,
            actor_account_id=None,
            action="meeting.delete",
            target="meeting:42",
            detail={"reason": "test"},
        )
        s.flush()
        assert entry.id is not None
        assert entry.action == "meeting.delete"
        assert entry.detail == {"reason": "test"}


def test_record_audit_defaults_detail_to_empty_dict():
    with get_session() as s:
        org_id = s.query(Organization).filter_by(name="default").one().id
        entry = record_audit(
            s,
            organization_id=org_id,
            actor_account_id=None,
            action="account.login",
            target="account:1",
        )
        s.flush()
        assert entry.detail == {}


def test_audit_log_module_exposes_no_mutator_besides_record_audit():
    import meeting_mgr.audit as mod

    public = [n for n in dir(mod) if not n.startswith("_")]
    assert public == ["record_audit"], (
        "the audit log is append-only: exactly one writer, no update or delete helper"
    )
