import pytest
from meeting_mgr.db import get_readonly_session
from meeting_mgr.models import KeyTopic, Meeting, Organization
from meeting_mgr.provenance import confirm

def test_confirm_promotes_inferred_to_confirmed():
    with get_readonly_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="published")
        s.add(m); s.flush()
        t = KeyTopic(meeting_id=m.id, title="budget", citations=[1],
                     provenance="inferred")
        s.add(t); s.flush()
        confirm(t)
        assert t.provenance == "confirmed"

def test_confirm_is_idempotent():
    with get_readonly_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="published")
        s.add(m); s.flush()
        t = KeyTopic(meeting_id=m.id, title="budget", citations=[1],
                     provenance="confirmed")
        s.add(t); s.flush()
        confirm(t)
        assert t.provenance == "confirmed"

def test_meeting_current_stage_defaults_to_none():
    with get_readonly_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="t", status="pending")
        s.add(m); s.flush()
        assert m.current_stage is None
