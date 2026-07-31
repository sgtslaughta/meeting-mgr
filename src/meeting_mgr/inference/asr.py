import httpx
from meeting_mgr.config import get_settings
from meeting_mgr.inference.llm import InferenceError, MAX_ATTEMPTS
import time

def transcribe_audio(audio: bytes, base_url: str | None = None) -> list[dict]:
    s = get_settings()
    url = f"{base_url or s.asr_base_url}/audio/transcriptions"
    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.post(
                url, timeout=600.0,
                headers={"authorization": f"Bearer {s.asr_api_key}"},
                files={"file": ("audio.wav", audio, "audio/wav")},
                data={"model": s.asr_model, "response_format": "verbose_json"},
            )
            r.raise_for_status()
            return r.json().get("segments", [])
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.5 * 2 ** attempt)
    raise InferenceError(f"{url} failed after {MAX_ATTEMPTS} attempts: {last}")
