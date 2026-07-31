import subprocess, tempfile, pathlib
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Meeting, Recording
from meeting_mgr.storage import get_object, put_object

class NormalizeError(Exception):
    pass

def normalize(meeting_id: int) -> None:
    with get_readonly_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        raw_key = rec.raw_key
    data = get_object(raw_key)
    with tempfile.TemporaryDirectory() as d:
        src = pathlib.Path(d) / "in"
        dst = pathlib.Path(d) / "out.wav"
        src.write_bytes(data)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)],
            capture_output=True,
        )
        if proc.returncode != 0 or not dst.exists():
            raise NormalizeError(proc.stderr.decode()[-800:])
        wav = dst.read_bytes()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(dst)], capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip())
    key = f"normalized/{meeting_id}.wav"
    put_object(key, wav)
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=meeting_id).one()
        rec.normalized_key, rec.duration_seconds = key, duration
        s.get(Meeting, meeting_id).status = "processing"
