import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting, Organization, Recording
from meeting_mgr.pipeline.normalize import _write_manifest_chunks, normalize
from meeting_mgr.storage import ensure_bucket, put_object


def _org() -> int:
    with get_session() as s:
        o = Organization(name=f"org-{uuid.uuid4()}")
        s.add(o)
        s.flush()
        return o.id


def _make_wav_bytes(seconds: float) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "gen.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=16000:cl=mono",
                "-t",
                str(seconds),
                str(out),
            ],
            capture_output=True,
            check=True,
        )
        return out.read_bytes()


def test_normalize_reconstructs_audio_from_a_chunk_manifest():
    # Kill: a manifest-backed Recording would 404 (raw_key literally has a
    # "manifest:" prefix that isn't a real object) unless normalize() branches
    # on it and reassembles the chunks before handing them to ffmpeg.
    ensure_bucket()
    org_id = _org()
    with get_session() as s:
        m = Meeting(organization_id=org_id, title="captured", status="pending")
        s.add(m)
        s.flush()
        meeting_id = m.id

    whole = _make_wav_bytes(2.0)
    midpoint = len(whole) // 2
    chunk_keys = [f"raw/{meeting_id}/chunks/000000.wav", f"raw/{meeting_id}/chunks/000001.wav"]
    put_object(chunk_keys[0], whole[:midpoint])
    put_object(chunk_keys[1], whole[midpoint:])
    manifest_key = f"raw/{meeting_id}/manifest.json"
    put_object(manifest_key, json.dumps(chunk_keys).encode())

    with get_session() as s:
        s.add(Recording(meeting_id=meeting_id, raw_key=f"manifest:{manifest_key}"))

    normalize(meeting_id)

    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        assert rec.normalized_key is not None
        assert rec.duration_seconds is not None
        assert 1.5 < rec.duration_seconds < 2.5


def test_write_manifest_chunks_preserves_order_with_many_chunks(tmp_path):
    # Kill: writing chunks in the wrong order (e.g. sorted alphabetically
    # instead of manifest order) would still "work" with 2 chunks if they
    # happen to sort correctly, but with 10 distinct byte blocks any
    # reordering shows up immediately in the reconstructed bytes.
    ensure_bucket()
    blocks = [bytes([i]) * 1000 for i in range(10)]
    keys = [f"raw/order-test/{uuid.uuid4()}/{i:06d}.bin" for i in range(10)]
    for key, block in zip(keys, blocks, strict=True):
        put_object(key, block)
    manifest_key = f"raw/order-test/{uuid.uuid4()}/manifest.json"
    put_object(manifest_key, json.dumps(keys).encode())

    out = tmp_path / "reconstructed.bin"
    with out.open("wb") as fh:
        _write_manifest_chunks(manifest_key, fh)

    assert out.read_bytes() == b"".join(blocks)


def test_write_manifest_chunks_reconstructs_around_a_gap(tmp_path):
    # Kill: a manifest listing only the chunks that actually made it (one
    # dropped mid-sequence) must reconstruct exactly those chunks, in order,
    # not raise and not silently include a missing/empty placeholder.
    ensure_bucket()
    block_a = b"A" * 1000
    block_c = b"C" * 1000
    key_a = f"raw/gap-test/{uuid.uuid4()}/000000.bin"
    key_c = f"raw/gap-test/{uuid.uuid4()}/000002.bin"  # 000001 never arrived
    put_object(key_a, block_a)
    put_object(key_c, block_c)
    manifest_key = f"raw/gap-test/{uuid.uuid4()}/manifest.json"
    put_object(manifest_key, json.dumps([key_a, key_c]).encode())

    out = tmp_path / "reconstructed.bin"
    with out.open("wb") as fh:
        _write_manifest_chunks(manifest_key, fh)

    assert out.read_bytes() == block_a + block_c
