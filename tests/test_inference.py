import pytest
from pydantic import BaseModel

from meeting_mgr.inference.asr import transcribe_audio
from meeting_mgr.inference.llm import InferenceError, structured_chat


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


def test_structured_chat_retries_when_choices_is_empty(fake_inference):
    for _ in range(3):
        fake_inference.push_raw({"choices": []})
    with pytest.raises(InferenceError):
        structured_chat("go", Topics, base_url=f"{fake_inference.base_url}/v1")
    assert len(fake_inference.requests) == 3, "should retry all 3 attempts"


def test_structured_chat_retries_when_body_is_not_an_object(fake_inference):
    for _ in range(3):
        fake_inference.push_raw(["not", "a", "dict"])
    with pytest.raises(InferenceError):
        structured_chat("go", Topics, base_url=f"{fake_inference.base_url}/v1")
    assert len(fake_inference.requests) == 3, "should retry all 3 attempts"


def test_transcribe_audio_returns_segments(fake_inference):
    fake_inference.push_transcription({"segments": [{"start": 0.0, "end": 1.5, "text": "hello"}]})
    out = transcribe_audio(b"WAVDATA", base_url=f"{fake_inference.base_url}/v1")
    assert out == [{"start": 0.0, "end": 1.5, "text": "hello"}]


def test_transcribe_audio_accepts_an_explicitly_empty_transcript(fake_inference):
    fake_inference.push_transcription({"segments": []})
    assert transcribe_audio(b"WAVDATA", base_url=f"{fake_inference.base_url}/v1") == []


def test_transcribe_audio_rejects_a_response_missing_segments(fake_inference):
    for _ in range(3):
        fake_inference.push_transcription({"text": "no segments key here"})
    with pytest.raises(InferenceError):
        transcribe_audio(b"WAVDATA", base_url=f"{fake_inference.base_url}/v1")


def test_transcribe_audio_does_not_swallow_unrelated_value_errors(monkeypatch, fake_inference):
    # json.JSONDecodeError subclasses ValueError; catching bare ValueError
    # (as asr.py used to) also swallows unrelated ValueErrors and masks them
    # as ordinary parse-failure retries. A genuine, non-JSON ValueError must
    # propagate immediately, uncaught and unretried.
    from meeting_mgr.inference import asr as asr_mod

    fake_inference.push_transcription({"segments": []})

    def boom(data):
        raise ValueError("unrelated failure")

    monkeypatch.setattr(asr_mod._AsrResponse, "model_validate", boom)
    with pytest.raises(ValueError, match="unrelated failure"):
        transcribe_audio(b"WAVDATA", base_url=f"{fake_inference.base_url}/v1")
    assert len(fake_inference.requests) == 1, "must not retry a non-JSON ValueError"
