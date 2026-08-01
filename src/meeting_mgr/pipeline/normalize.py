import io
import json
import pathlib
import subprocess
import tempfile

from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Meeting, Recording
from meeting_mgr.storage import append_stream, get_stream, put_stream


class NormalizeError(Exception):
    pass


def _write_manifest_chunks(manifest_key: str, fh) -> None:
    """Reconstruct a browser-captured Recording from its ordered chunk
    manifest (api/capture.py's finish_capture). MediaRecorder's chunks are
    byte-continuations of one stream -- the container header lives only in
    the first chunk -- so writing them to fh in manifest order reproduces
    the original file exactly. Streams each chunk through fh (a real file on
    disk) exactly like the single-file get_stream() path: peak memory is one
    chunk, never the whole recording. The manifest itself is a tiny JSON
    array (a list of keys, not audio), so it's read via get_stream() into an
    in-memory buffer rather than storage.get_object() -- this module is kept
    free of get_object/put_object entirely (see test_normalize_streams_both_ways)."""
    buf = io.BytesIO()
    get_stream(manifest_key, buf)
    manifest = json.loads(buf.getvalue())
    for key in manifest:
        # IMPORTANT: get_stream()/download_fileobj is NOT used here. It
        # writes each transfer from fh's offset 0 rather than fh's current
        # position -- verified empirically: two get_stream() calls into one
        # handle silently overwrite instead of appending, even for two
        # ~1 KB objects, well under any multipart threshold. append_stream()
        # streams the raw response body via shutil.copyfileobj, which uses
        # ordinary sequential fh.write() and so respects wherever fh already
        # is. See storage.append_stream's docstring for the full story.
        append_stream(key, fh)


def normalize(meeting_id: int) -> None:
    with get_readonly_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key = rec.raw_key
    key = f"normalized/{meeting_id}.wav"
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "in"
        dst = pathlib.Path(d) / "out.wav"
        # Stream both ways through the temp dir. A 16 kHz mono WAV runs about
        # 115 MB per hour of meeting, and ffmpeg needs a real file anyway.
        with src.open("wb") as fh:
            if raw_key.startswith("manifest:"):
                _write_manifest_chunks(raw_key.removeprefix("manifest:"), fh)
            else:
                get_stream(raw_key, fh)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
            capture_output=True,
        )
        if proc.returncode != 0 or not dst.exists():
            raise NormalizeError(proc.stderr.decode()[-800:])
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(dst),
            ],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            raise NormalizeError(f"ffprobe failed: {probe.stderr.strip()[-800:]}")
        try:
            duration = float(probe.stdout.strip())
        except ValueError as e:
            # NormalizeError is this stage's documented failure contract; a raw
            # ValueError would escape every caller that honours it.
            raise NormalizeError(f"ffprobe reported no usable duration: {probe.stdout!r}") from e
        with dst.open("rb") as fh:
            put_stream(key, fh)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        rec.normalized_key, rec.duration_seconds = key, duration
        s.get(Meeting, meeting_id).status = "processing"
