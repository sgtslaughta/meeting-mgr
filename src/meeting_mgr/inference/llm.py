import json, time
import httpx
from pydantic import BaseModel, ValidationError
from meeting_mgr.config import get_settings

class InferenceError(Exception):
    pass

MAX_ATTEMPTS = 3

def structured_chat(prompt: str, schema: type[BaseModel], base_url: str | None = None) -> BaseModel:
    s = get_settings()
    url = f"{base_url or s.llm_base_url}/chat/completions"
    last = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.post(
                url, timeout=120.0,
                headers={"authorization": f"Bearer {s.llm_api_key}"},
                json={"model": s.llm_model,
                      "messages": [{"role": "user", "content": prompt}],
                      "response_format": {"type": "json_object"}},
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return schema.model_validate(json.loads(content))
        # A 200 with the wrong shape is as much a failure as a 500: IndexError
        # (empty `choices`) and TypeError (body is not a dict) must retry too,
        # so InferenceError stays the only failure mode callers see.
        except (httpx.HTTPError, KeyError, IndexError, TypeError,
                json.JSONDecodeError, ValidationError) as e:
            last = e
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(0.5 * 2 ** attempt)
    raise InferenceError(f"{url} failed after {MAX_ATTEMPTS} attempts: {last}") from last
