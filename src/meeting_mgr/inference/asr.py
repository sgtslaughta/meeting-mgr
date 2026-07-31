import time

import httpx
from pydantic import BaseModel, ValidationError

from meeting_mgr.config import get_settings
from meeting_mgr.inference.llm import MAX_ATTEMPTS, InferenceError


class _AsrSegment(BaseModel):
    start: float
    end: float
    text: str


class _AsrResponse(BaseModel):
    segments: list[_AsrSegment]


def transcribe_audio(audio, base_url: str | None = None) -> list[dict]:
    """Send audio to the ASR endpoint and return its segments.

    `audio` may be raw bytes or an open file-like object; httpx streams
    either directly into the multipart body, so callers with large files
    can pass an open handle instead of buffering the whole recording.
    """
    s = get_settings()
    url = f"{base_url or s.asr_base_url}/audio/transcriptions"
    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.post(
                url,
                timeout=600.0,
                headers={"authorization": f"Bearer {s.asr_api_key}"},
                files={"file": ("audio.wav", audio, "audio/wav")},
                data={"model": s.asr_model, "response_format": "verbose_json"},
            )
            r.raise_for_status()
            # Validate the shape rather than defaulting a missing `segments` to
            # []. A silent meeting returns `segments: []`; a garbled response
            # has no `segments` at all, and the two must not look the same.
            data = _AsrResponse.model_validate(r.json())
            return [seg.model_dump() for seg in data.segments]
        except (httpx.HTTPError, ValueError, ValidationError) as e:
            last = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.5 * 2**attempt)
    raise InferenceError(f"{url} failed after {MAX_ATTEMPTS} attempts: {last}") from last
