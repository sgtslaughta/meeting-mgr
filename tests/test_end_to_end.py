import re
import subprocess
import uuid

from fastapi.testclient import TestClient

from meeting_mgr.api.main import app
from meeting_mgr.auth.password import hash_password
from meeting_mgr.db import get_session
from meeting_mgr.models import Account, Organization
from meeting_mgr.pipeline import attribute as attr_mod
from meeting_mgr.pipeline import diarize as di
from meeting_mgr.pipeline import extract as ex
from meeting_mgr.pipeline import orchestrate as orch
from meeting_mgr.pipeline import transcribe as tr


def _account_and_client() -> TestClient:
    email = f"e2e-{uuid.uuid4()}@x.com"
    with get_session() as s:
        org = s.query(Organization).filter_by(name="default").one()
        s.add(Account(organization_id=org.id, email=email, password_hash=hash_password("pw")))
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200
    return c


def test_upload_to_published_record(tmp_path, monkeypatch):
    src = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(src)],
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(
        di,
        "_call_diarizer",
        lambda fileobj: {
            "clusters": [
                {"label": "SPEAKER_00", "embedding": [0.1], "spans": [{"start": 0.0, "end": 2.0}]}
            ]
        },
    )
    monkeypatch.setattr(
        tr,
        "transcribe_audio",
        lambda audio: [{"start": 0.0, "end": 2.0, "text": "Sarah will ship the migration"}],
    )
    monkeypatch.setattr(
        attr_mod,
        "structured_chat",
        lambda p, schema, **kw: schema.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": "Sarah"}]}
        ),
    )

    def fake_extract(prompt, schema, **kw):
        name = schema.__name__
        # Read the id from the transcript we were handed, like a real model
        # would — rather than querying for the globally-latest Segment.
        m = re.search(r"\[(\d+)\]\[", prompt)
        assert m, "prompt must contain a cited transcript line"
        seg_id = int(m.group(1))
        return schema.model_validate(
            {
                "TopicsOut": {"topics": [{"title": "migration", "citations": [seg_id]}]},
                "MinutesOut": {"minutes": [{"text": "Sarah committed", "citations": [seg_id]}]},
                "ActionItemsOut": {
                    "action_items": [
                        {
                            "text": "ship the migration",
                            "participant_name": "Sarah",
                            "citations": [seg_id],
                        }
                    ]
                },
                "DecisionPointsOut": {
                    "decision_points": [
                        {"text": "ship now", "settled": True, "citations": [seg_id]}
                    ]
                },
            }[name]
        )

    monkeypatch.setattr(ex, "structured_chat", fake_extract)

    c = _account_and_client()
    monkeypatch.setattr("meeting_mgr.api.meetings.run_pipeline", orch.run_pipeline)
    r = c.post(
        "/meetings",
        data={"title": "standup"},
        files={"file": ("tone.wav", src.read_bytes(), "audio/wav")},
    )
    mid = r.json()["meeting_id"]

    body = c.get(f"/meetings/{mid}").json()
    assert body["status"] == "published"
    assert body["segments"][0]["text"] == "Sarah will ship the migration"
    seg_id = body["segments"][0]["id"]
    assert body["action_items"][0]["citations"] == [seg_id]
    assert body["action_items"][0]["provenance"] == "inferred"
    assert body["key_topics"][0]["title"] == "migration"
    assert body["key_topics"][0]["citations"] == [seg_id]
    assert body["key_topics"][0]["provenance"] == "inferred"
    assert body["minutes"][0]["text"] == "Sarah committed"
    assert body["minutes"][0]["citations"] == [seg_id]
    assert body["minutes"][0]["provenance"] == "inferred"
    assert body["decision_points"][0]["settled"] is True
    assert body["decision_points"][0]["citations"] == [seg_id]
    assert body["decision_points"][0]["provenance"] == "inferred"
