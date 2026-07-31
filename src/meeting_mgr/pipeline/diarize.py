import pathlib
import tempfile

import httpx

from meeting_mgr.config import get_settings
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Recording, SpeakerCluster
from meeting_mgr.storage import get_stream


class DiarizeError(Exception):
    pass


def _call_diarizer(fileobj) -> dict:
    """POST an open audio file to the diarizer. httpx streams the file object,
    so an hour of audio never lands in memory as one buffer."""
    url = f"{get_settings().diarizer_url}/diarize"
    try:
        r = httpx.post(url, files={"file": ("a.wav", fileobj, "audio/wav")}, timeout=3600.0)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise DiarizeError(f"{url}: {e}") from e


def diarize(meeting_id: int) -> None:
    with get_readonly_session() as s:
        key = s.query(Recording).filter_by(meeting_id=meeting_id).one().normalized_key
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "audio.wav"
        with path.open("wb") as fh:
            get_stream(key, fh)
        with path.open("rb") as fh:
            result = _call_diarizer(fh)
    with get_session() as s:
        for c in result["clusters"]:
            s.add(
                SpeakerCluster(
                    meeting_id=meeting_id,
                    label=c["label"],
                    embedding=c.get("embedding"),
                    spans=c.get("spans", []),
                )
            )
