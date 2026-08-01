import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from meeting_mgr.bot_credentials import create_bot_credential
from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Account, BotSession, Meeting, Organization


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _account(org_id: int) -> int:
    with get_session() as s:
        a = Account(organization_id=org_id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return a.id


def _credential_and_meeting(org_id, account_id):
    with get_session() as s:
        # label must be unique per (organization_id, label) as of issue #39
        # (uq_bot_credential_org_label); a fresh label per call keeps this
        # helper safe to call more than once for the same org.
        cred, _ = create_bot_credential(
            s, org_id, label=f"a-{uuid.uuid4()}", owner_account_id=account_id
        )
        m = Meeting(
            organization_id=org_id, owner_account_id=account_id, title="t", status="capturing"
        )
        s.add(m)
        s.flush()
        return cred.id, m.id


def test_bot_session_idempotency_key_is_unique_per_credential():
    org_id = _org()
    account_id = _account(org_id)
    cred_id, meeting_id_1 = _credential_and_meeting(org_id, account_id)
    _, meeting_id_2 = _credential_and_meeting(org_id, account_id)
    with pytest.raises(IntegrityError):
        with get_session() as s:
            s.add(
                BotSession(
                    organization_id=org_id,
                    bot_credential_id=cred_id,
                    meeting_id=meeting_id_1,
                    platform_meeting_id="zoom-123",
                )
            )
            s.add(
                BotSession(
                    organization_id=org_id,
                    bot_credential_id=cred_id,
                    meeting_id=meeting_id_2,
                    platform_meeting_id="zoom-123",
                )
            )
            s.flush()


def test_bot_session_is_deleted_when_its_meeting_is_deleted():
    org_id = _org()
    account_id = _account(org_id)
    cred_id, meeting_id = _credential_and_meeting(org_id, account_id)
    with get_session() as s:
        s.add(
            BotSession(
                organization_id=org_id,
                bot_credential_id=cred_id,
                meeting_id=meeting_id,
                platform_meeting_id="zoom-123",
            )
        )

    with get_session() as s:
        s.query(Meeting).filter_by(id=meeting_id).delete()

    with get_session() as s:
        assert s.query(BotSession).filter_by(meeting_id=meeting_id).count() == 0, (
            "ON DELETE CASCADE from meeting did not remove the BotSession row"
        )


def test_bot_session_tenant_isolation():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    cred_a, meeting_a = _credential_and_meeting(org_a, account_a)
    cred_b, meeting_b = _credential_and_meeting(org_b, account_b)
    with get_session() as s:
        s.add(
            BotSession(
                organization_id=org_a,
                bot_credential_id=cred_a,
                meeting_id=meeting_a,
                platform_meeting_id="a",
            )
        )
        s.add(
            BotSession(
                organization_id=org_b,
                bot_credential_id=cred_b,
                meeting_id=meeting_b,
                platform_meeting_id="b",
            )
        )

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT platform_meeting_id FROM bot_session")).fetchall()
        platform_ids = {r[0] for r in rows}
    assert "a" in platform_ids
    assert "b" not in platform_ids, "RLS did not confine bot_session to its own organization"
