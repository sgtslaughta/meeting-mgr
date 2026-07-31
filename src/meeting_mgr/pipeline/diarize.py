import httpx
from meeting_mgr.config import get_settings
from meeting_mgr.db import get_readonly_session, get_session
from meeting_mgr.models import Recording, SpeakerCluster
from meeting_mgr.storage import get_object


class DiarizeError(Exception):
    pass


def _call_diarizer(audio: bytes) -> dict:
    url = f"{get_settings().diarizer_url}/diarize"
    try:
        r = httpx.post(url, files={"file": ("a.wav", audio, "audio/wav")}, timeout=3600.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise DiarizeError(f"{url}: {e}") from e


def diarize(meeting_id: int) -> None:
    with get_readonly_session() as s:
        key = s.query(Recording).filter_by(meeting_id=meeting_id).one().normalized_key
    result = _call_diarizer(get_object(key))
    with get_session() as s:
        for c in result["clusters"]:
            s.add(SpeakerCluster(
                meeting_id=meeting_id, label=c["label"],
                embedding=c.get("embedding"), spans=c.get("spans", []),
            ))
