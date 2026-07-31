from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting, Organization, ActionItem

def test_action_item_requires_citations_and_provenance():
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="standup", status="pending")
        s.add(m); s.flush()
        item = ActionItem(
            meeting_id=m.id, text="ship the migration",
            citations=[1, 2], provenance="inferred",
        )
        s.add(item); s.flush()
        assert item.citations == [1, 2]
        assert item.provenance == "inferred"

def test_action_item_due_date_roundtrips_as_date():
    from datetime import date as _date
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        m = Meeting(organization_id=org.id, title="planning", status="pending")
        s.add(m); s.flush()
        item = ActionItem(
            meeting_id=m.id, text="ship it", citations=[],
            provenance="inferred", due_date=_date(2026, 8, 15),
        )
        s.add(item); s.flush()
        s.expire(item)
        assert item.due_date == _date(2026, 8, 15)
        assert isinstance(item.due_date, _date)
