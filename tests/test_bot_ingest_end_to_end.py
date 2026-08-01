import io
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.bot_credentials import create_bot_credential
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, BotSession, Meeting, Organization, Recording
from meeting_mgr.pipeline.normalize import _write_manifest_chunks, normalize
from meeting_mgr.pipeline.purge import purge_meeting_full
from meeting_mgr.retention import select_purge_candidates, upsert_policy
from meeting_mgr.storage import ensure_bucket

CHUNK_COUNT = 12  # cross ten: "10" sorts before "9" lexically, so <=9 chunks
# would pass even if the manifest were built on lexical, not numeric, order.


def _position_marked_chunks(count: int) -> list[bytes]:
    """Each chunk's bytes encode its own sequence number, not just an equal
    share of some total -- a WAV's duration is a function of total byte
    count only, so a duration-shaped assertion can't tell chunk 10 landing
    between chunk 1 and chunk 2 from chunk 10 landing where it belongs. A
    direct byte-for-byte comparison against the expected in-order
    concatenation can."""
    return [f"chunk-{seq:04d}-".encode() + bytes([seq % 256]) * 64 for seq in range(count)]


def _org_account() -> tuple[int, int]:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        a = Account(organization_id=o.id, email=f"{uuid.uuid4()}@x.com", role="admin")
        s.add(a)
        s.flush()
        return o.id, a.id


def _wav_chunks(seconds_total: float, count: int) -> list[bytes]:
    """A real WAV, split into byte-slices -- not one slice per audio chunk
    (that would round-trip fine even with chunks concatenated out of order,
    since silence is silence). Each slice is a distinct region of a tone
    sweep so mis-ordering would produce audibly/structurally wrong output,
    same reasoning as test_write_manifest_chunks_preserves_order_with_many_chunks."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "gen.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={seconds_total}",
                str(out),
            ],
            capture_output=True,
            check=True,
        )
        whole = out.read_bytes()
    size = len(whole)
    step = size // count
    chunks = [whole[i * step : (i + 1) * step] for i in range(count - 1)]
    chunks.append(whole[(count - 1) * step :])
    return chunks


def _finished_bot_meeting(monkeypatch, chunks: list[bytes]) -> tuple[int, int]:
    import meeting_mgr.api.bot as bot_module

    monkeypatch.setattr(bot_module, "run_pipeline", lambda meeting_id: None)
    ensure_bucket()
    org_id, account_id = _org_account()
    with get_session() as s:
        _, token = create_bot_credential(s, org_id, label="bot", owner_account_id=account_id)
    c = TestClient(app)
    headers = {"authorization": f"Bearer {token}"}
    body = c.post(
        "/bot/sessions", json={"platform_meeting_id": "z-1", "title": "t"}, headers=headers
    ).json()
    session_id, meeting_id = body["session_id"], body["meeting_id"]
    for seq, data in enumerate(chunks):
        r = c.put(
            f"/bot/sessions/{session_id}/chunks/{seq}",
            headers=headers,
            files={"chunk": ("c.bin", io.BytesIO(data), "application/octet-stream")},
        )
        assert r.status_code == 200
    r = c.post(f"/bot/sessions/{session_id}/finish", headers=headers)
    assert r.status_code == 200
    return org_id, meeting_id


def test_bot_meeting_is_a_purge_candidate_like_any_other(monkeypatch):
    # Kill: removing the "full" purge-candidate classification for a
    # bot-ingested Meeting (or excluding bot meetings from the candidate
    # query entirely) empties this list.
    org_id, meeting_id = _finished_bot_meeting(monkeypatch, [b"x"] * CHUNK_COUNT)
    with get_session() as s:
        s.get(Meeting, meeting_id).created_at = datetime.utcnow() - timedelta(days=400)
    with get_session() as s:
        upsert_policy(s, org_id, audio_retention_days=None, meeting_retention_days=30)
        candidates = select_purge_candidates(s, org_id)
    assert [c.meeting_id for c in candidates] == [meeting_id]
    assert candidates[0].kind == "full"


def test_purge_meeting_full_deletes_the_bot_session_row_via_cascade(monkeypatch):
    # Kill: removing BotSession from the meeting_id ON DELETE CASCADE chain
    # (or purge_meeting_full failing to delete the Meeting row) leaves this
    # row behind.
    org_id, meeting_id = _finished_bot_meeting(monkeypatch, [b"x"] * CHUNK_COUNT)
    with get_session() as s:
        assert s.query(BotSession).filter_by(meeting_id=meeting_id).count() == 1

    purge_meeting_full(org_id, meeting_id)

    with get_session() as s:
        assert s.get(Meeting, meeting_id) is None
        assert s.query(BotSession).filter_by(meeting_id=meeting_id).count() == 0


def test_normalize_reads_a_bot_written_manifest_raw_key_unmodified(monkeypatch):
    """normalize() already branches on the "manifest:" prefix (Phase 5); a
    bot-created manifest uses the identical prefix and object shape, so this
    must pass with zero changes to pipeline/normalize.py.

    This only proves normalize() runs end to end against a bot-produced
    manifest recording and produces a plausible-duration output -- WAV
    duration is data_size / (sample_rate * channels * bytes_per_sample), a
    function of total byte count, not content order, so this assertion
    cannot detect chunks reassembled out of order. See
    test_bot_chunks_reassemble_byte_for_byte_in_upload_order for that.

    Kill: deleting the "manifest:" branch in normalize.py breaks this."""
    chunks = _wav_chunks(2.0, CHUNK_COUNT)
    org_id, meeting_id = _finished_bot_meeting(monkeypatch, chunks)
    normalize(meeting_id)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        assert rec.normalized_key is not None
        assert rec.duration_seconds is not None
        assert 1.5 < rec.duration_seconds < 2.5


def test_bot_chunks_reassemble_byte_for_byte_in_upload_order(monkeypatch, tmp_path):
    """Byte-for-byte proof that the manifest reconstruction pipeline
    (pipeline/normalize.py's _write_manifest_chunks, the exact function
    normalize() calls) reassembles a bot-uploaded manifest in upload order,
    not lexical key order -- with distinguishable per-chunk content, not a
    duration-shaped proxy that survives scrambling.

    Kill: dropping the zero-padding in _bot_chunk_key ("{seq:06d}" ->
    "{seq}") alone leaves this green -- finish_session's explicit
    key=lambda k: _bot_chunk_seq(prefix, k) sorts on the parsed integer, not
    the key string, so it is unaffected by padding. Changing finish_session
    to a plain sorted(list_keys(prefix)) (no key=) also alone leaves this
    green -- with padding intact, lexical and numeric order agree for any
    seq below 10**6. Only doing BOTH together (as in the original bug this
    guards) turns it red: verified locally, reverted before commit."""
    chunks = _position_marked_chunks(CHUNK_COUNT)
    org_id, meeting_id = _finished_bot_meeting(monkeypatch, chunks)

    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        manifest_key = rec.raw_key.removeprefix("manifest:")

    out = tmp_path / "reconstructed.bin"
    with out.open("wb") as fh:
        _write_manifest_chunks(manifest_key, fh)

    assert out.read_bytes() == b"".join(chunks)
