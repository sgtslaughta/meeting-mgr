import uuid

from sqlalchemy import text

from meeting_mgr.db import get_org_session, get_session
from meeting_mgr.models import Account, BotCredential, Organization


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


def test_bot_credential_defaults_unrevoked():
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        c = BotCredential(
            organization_id=org_id,
            owner_account_id=account_id,
            label="zoom-bot-1",
            token_hash="x",
        )
        s.add(c)
        s.flush()
        assert c.revoked_at is None


def test_bot_credential_tenant_isolation():
    org_a, org_b = _org(), _org()
    account_a, account_b = _account(org_a), _account(org_b)
    with get_session() as s:
        s.add(
            BotCredential(
                organization_id=org_a, owner_account_id=account_a, label="a", token_hash="x"
            )
        )
        s.add(
            BotCredential(
                organization_id=org_b, owner_account_id=account_b, label="b", token_hash="x"
            )
        )

    with get_org_session(org_a) as s:
        rows = s.execute(text("SELECT label FROM bot_credential")).fetchall()
        labels = {r[0] for r in rows}
    assert "a" in labels
    assert "b" not in labels, "RLS did not confine bot_credential to its own organization"
