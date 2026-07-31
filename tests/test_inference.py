import pytest
from pydantic import BaseModel
from meeting_mgr.inference.llm import structured_chat, InferenceError

class Topics(BaseModel):
    topics: list[str]

def test_structured_chat_validates(fake_inference):
    fake_inference.push_chat({"topics": ["budget", "hiring"]})
    out = structured_chat("go", Topics, base_url=f"{fake_inference.base_url}/v1")
    assert out.topics == ["budget", "hiring"]

def test_structured_chat_retries_then_raises(fake_inference):
    for _ in range(3):
        fake_inference.push_error(500)
    with pytest.raises(InferenceError):
        structured_chat("go", Topics, base_url=f"{fake_inference.base_url}/v1")
    assert len(fake_inference.requests) == 3
