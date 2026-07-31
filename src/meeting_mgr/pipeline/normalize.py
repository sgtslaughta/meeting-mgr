import pathlib
import subprocess
import tempfile

from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Meeting, Recording
from meeting_mgr.storage import get_stream, put_stream


class NormalizeError(Exception):
    pass


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
