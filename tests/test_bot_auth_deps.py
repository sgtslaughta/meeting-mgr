import uuid
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from meeting_mgr.auth import bot_deps
from meeting_mgr.auth.bot_deps import get_bot_credential
from meeting_mgr.bot_credentials import create_bot_credential, revoke_bot_credential
from meeting_mgr.db import get_session
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


def _request_with_bearer(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def _make_credential() -> tuple[int, int, str]:
    org_id = _org()
    account_id = _account(org_id)
    with get_session() as s:
        cred, token = create_bot_credential(s, org_id, label="a", owner_account_id=account_id)
        return cred.id, org_id, token


def test_a_valid_token_resolves_to_its_credential():
    cred_id, org_id, token = _make_credential()
    resolved = get_bot_credential(_request_with_bearer(token))
    assert resolved.id == cred_id
    assert resolved.organization_id == org_id


def test_a_correct_id_with_a_wrong_secret_is_rejected():
    cred_id, _org_id, token = _make_credential()
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer(f"{cred_id}.wrong-secret"))
    assert exc.value.status_code == 401


def test_a_revoked_credential_is_rejected_even_with_the_correct_secret():
    cred_id, org_id, token = _make_credential()
    with get_session() as s:
        assert s.get(BotCredential, cred_id).organization_id == org_id
    with get_session() as s:
        revoke_bot_credential(s, org_id, cred_id)

    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer(token))
    assert exc.value.status_code == 401


def test_a_missing_header_is_rejected():
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer(None))
    assert exc.value.status_code == 401


def test_a_malformed_token_with_no_dot_is_rejected():
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer("not-a-valid-token"))
    assert exc.value.status_code == 401


def test_a_non_integer_credential_id_is_rejected():
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer("not-an-int.some-secret"))
    assert exc.value.status_code == 401


def test_an_unknown_credential_id_is_rejected():
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer("999999999.some-secret"))
    assert exc.value.status_code == 401


def _rejection(token: str | None) -> tuple[int, object]:
    with pytest.raises(HTTPException) as exc:
        get_bot_credential(_request_with_bearer(token))
    return exc.value.status_code, exc.value.detail


def test_every_rejection_path_is_indistinguishable():
    cred_id, org_id, token = _make_credential()
    with get_session() as s:
        revoke_bot_credential(s, org_id, cred_id)
    revoked_token = token

    other_cred_id, _other_org_id, _other_token = _make_credential()
    wrong_secret_token = f"{other_cred_id}.wrong-secret"

    results = [
        _rejection(None),
        _rejection("not-a-valid-token"),
        _rejection("not-an-int.some-secret"),
        _rejection("999999999.some-secret"),
        _rejection(revoked_token),
        _rejection(wrong_secret_token),
    ]

    first = results[0]
    for result in results[1:]:
        assert result == first


def test_verify_password_runs_on_every_rejection_path():
    """Structural stand-in for a timing assertion: spy on verify_password
    and confirm it is invoked on every rejection path, including ones that
    could otherwise short-circuit before any hashing (missing header,
    malformed token, unknown id, revoked credential) -- the same class of
    oracle api/auth.py's login() guards against for account enumeration.
    """
    cred_id, org_id, token = _make_credential()
    with get_session() as s:
        revoke_bot_credential(s, org_id, cred_id)

    cases = [None, "not-a-valid-token", "not-an-int.secret", "999999999.secret", token]

    for case in cases:
        with patch.object(bot_deps, "verify_password", wraps=bot_deps.verify_password) as spy:
            with pytest.raises(HTTPException):
                get_bot_credential(_request_with_bearer(case))
            assert spy.called, f"verify_password was not called for token={case!r}"
