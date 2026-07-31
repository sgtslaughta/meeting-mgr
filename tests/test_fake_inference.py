import httpx

def test_chat_returns_queued_response(fake_inference):
    fake_inference.push_chat({"topics": ["budget"]})
    r = httpx.post(f"{fake_inference.base_url}/v1/chat/completions",
                   json={"model": "m", "messages": []})
    assert r.status_code == 200
    assert "budget" in r.json()["choices"][0]["message"]["content"]
