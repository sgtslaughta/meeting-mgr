import io
import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.bot_credentials import create_bot_credential
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, BotSession, Organization
from meeting_mgr.storage import ensure_bucket, get_object


def _org_account() -> tuple[int, int]:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        a = Account(organization_id=o.id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return o.id, a.id


def _client_and_session(label="bot"):
    ensure_bucket()
    org_id, account_id = _org_account()
    with get_session() as s:
        _, token = create_bot_credential(s, org_id, label=label, owner_account_id=account_id)
    c = TestClient(app)
    headers = {"authorization": f"Bearer {token}"}
    r = c.post("/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"}, headers=headers)
    body = r.json()
    return c, headers, body["session_id"], body["meeting_id"]


def _two_credentials_same_org():
    """Two BotCredentials under the SAME organization -- unlike
    _client_and_session()'s pairs (which also mint a fresh org each call,
    so bot_credential_id and organization_id differ together), this isolates
    the bot_credential_id half of _owned_session's filter. Without it, an
    organization_id-only filter would already 404 in every existing
    ownership test, leaving that half of the filter unpinned."""
    ensure_bucket()
    org_id, account_id = _org_account()
    with get_session() as s:
        _, token_a = create_bot_credential(
            s, org_id, label="bot-same-org-a", owner_account_id=account_id
        )
        _, token_b = create_bot_credential(
            s, org_id, label="bot-same-org-b", owner_account_id=account_id
        )
    c = TestClient(app)
    headers_a = {"authorization": f"Bearer {token_a}"}
    headers_b = {"authorization": f"Bearer {token_b}"}
    r = c.post(
        "/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"}, headers=headers_a
    )
    body = r.json()
    return c, headers_a, headers_b, body["session_id"]


def test_a_chunk_uploads_and_is_retrievable_from_storage():
    c, headers, session_id, meeting_id = _client_and_session()
    r = c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"audio-bytes"), "application/octet-stream")},
    )
    assert r.status_code == 200
    assert r.json() == {"seq": 0}
    assert get_object(f"raw/{meeting_id}/bot-chunks/000000.chunk") == b"audio-bytes"


def test_uploading_bumps_last_activity_at():
    c, headers, session_id, meeting_id = _client_and_session()
    with get_session() as s:
        before = s.query(BotSession).filter_by(id=session_id).one().last_activity_at

    c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )

    with get_session() as s:
        after = s.query(BotSession).filter_by(id=session_id).one().last_activity_at
    assert after > before


def test_listing_returns_uploaded_sequence_numbers_in_order():
    c, headers, session_id, meeting_id = _client_and_session()
    for seq in (2, 0, 1):
        c.put(
            f"/bot/sessions/{session_id}/chunks/{seq}",
            headers=headers,
            files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
        )
    r = c.get(f"/bot/sessions/{session_id}/chunks", headers=headers)
    assert r.json() == {"seqs": [0, 1, 2]}


def test_listing_sorts_numerically_not_lexically_past_ten():
    # "10" sorts before "9" lexically -- exercise the parsed-int sort path,
    # not just single-digit sequences where the two orders happen to agree.
    c, headers, session_id, meeting_id = _client_and_session()
    for seq in (9, 10, 2):
        c.put(
            f"/bot/sessions/{session_id}/chunks/{seq}",
            headers=headers,
            files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
        )
    r = c.get(f"/bot/sessions/{session_id}/chunks", headers=headers)
    assert r.json() == {"seqs": [2, 9, 10]}


def test_multichunk_upload_reassembles_in_order():
    # A single-chunk test would pass even if storage silently overwrote
    # instead of storing distinct chunks -- this needs >= 2 chunks, fetched
    # back and concatenated in sequence order, to prove each chunk landed at
    # its own key rather than clobbering a shared one.
    c, headers, session_id, meeting_id = _client_and_session()
    payloads = {0: b"first-chunk-bytes", 1: b"second-chunk-bytes", 2: b"third-chunk-bytes"}
    for seq, data in payloads.items():
        r = c.put(
            f"/bot/sessions/{session_id}/chunks/{seq}",
            headers=headers,
            files={"chunk": ("c.bin", io.BytesIO(data), "application/octet-stream")},
        )
        assert r.status_code == 200

    listed = c.get(f"/bot/sessions/{session_id}/chunks", headers=headers).json()["seqs"]
    assert listed == [0, 1, 2]

    reassembled = b"".join(
        get_object(f"raw/{meeting_id}/bot-chunks/{seq:06d}.chunk") for seq in listed
    )
    assert reassembled == b"".join(payloads[seq] for seq in listed)


def test_a_session_belonging_to_another_credential_is_not_found():
    c1, headers1, session_id, _ = _client_and_session(label="bot-a")
    _, headers2, _, _ = _client_and_session(label="bot-b")
    r = c1.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers2,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 404


def test_listing_for_a_session_belonging_to_another_credential_is_not_found():
    c1, headers1, session_id, _ = _client_and_session(label="bot-c")
    _, headers2, _, _ = _client_and_session(label="bot-d")
    r = c1.get(f"/bot/sessions/{session_id}/chunks", headers=headers2)
    assert r.status_code == 404


def test_a_session_belonging_to_another_credential_in_the_same_org_is_not_found_for_chunk_upload():
    # Kill: dropping bot_credential_id from _owned_session's filter (leaving
    # only organization_id, which both credentials here share) turns this
    # 404 into a 200 -- see _owned_session in src/meeting_mgr/api/bot.py.
    c, headers_a, headers_b, session_id = _two_credentials_same_org()
    r = c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers_b,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 404


def test_a_session_belonging_to_another_credential_in_the_same_org_is_not_found_for_listing():
    c, headers_a, headers_b, session_id = _two_credentials_same_org()
    r = c.get(f"/bot/sessions/{session_id}/chunks", headers=headers_b)
    assert r.status_code == 404


def test_a_session_belonging_to_another_credential_in_the_same_org_is_not_found_for_finish():
    c, headers_a, headers_b, session_id = _two_credentials_same_org()
    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers_b)
    assert r.status_code == 404


def test_chunk_upload_without_credentials_is_rejected():
    c, headers, session_id, _ = _client_and_session()
    r = c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 401


def test_a_chunk_upload_after_finish_is_rejected(monkeypatch):
    import meeting_mgr.api.bot as bot_module

    monkeypatch.setattr(bot_module, "run_pipeline", lambda meeting_id: None)
    c, headers, session_id, meeting_id = _client_and_session()
    c.put(
        f"/bot/sessions/{session_id}/chunks/0",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    c.post(f"/bot/sessions/{session_id}/finish", headers=headers)

    r = c.put(
        f"/bot/sessions/{session_id}/chunks/1",
        headers=headers,
        files={"chunk": ("c.bin", io.BytesIO(b"x"), "application/octet-stream")},
    )
    assert r.status_code == 409
