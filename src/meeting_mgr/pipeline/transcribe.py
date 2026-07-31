import pathlib
import tempfile

from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.inference.asr import transcribe_audio
from meeting_mgr.models import Recording, Segment
from meeting_mgr.storage import get_stream


def transcribe(meeting_id: int) -> None:
    with get_readonly_session() as s:
        key = s.query(Recording).filter_by(meeting_id=meeting_id).one().normalized_key
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "audio.wav"
        with path.open("wb") as fh:
            get_stream(key, fh)
        with path.open("rb") as fh:
            segments = transcribe_audio(fh)
    with get_session() as s:
        for seg in segments:
            s.add(
                Segment(
                    meeting_id=meeting_id,
                    cluster_id=None,
                    start_seconds=float(seg["start"]),
                    end_seconds=float(seg["end"]),
                    text=seg["text"].strip(),
                )
            )
