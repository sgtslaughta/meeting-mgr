import pytest
from meeting_mgr.db import get_session
from meeting_mgr.models import Meeting
from meeting_mgr.pipeline import orchestrate as orch

def test_pipeline_runs_all_stages_and_publishes(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    called = []
    monkeypatch.setattr(orch, "STAGES",
        [(name, lambda m, n=name: called.append(n))
         for name in ("normalize", "diarize")] + [("publish", orch.publish)])
    orch.run_pipeline(mid)
    assert called == ["normalize", "diarize"]
    with get_session() as s:
        assert s.get(Meeting, mid).status == "published"

def test_failure_records_stage_and_reraises(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    def boom(_): raise RuntimeError("ffmpeg died")
    monkeypatch.setattr(orch, "STAGES", [("normalize", boom)])
    with pytest.raises(RuntimeError):
        orch.run_pipeline(mid)
    with get_session() as s:
        m = s.get(Meeting, mid)
        assert m.status == "failed" and m.failed_stage == "normalize"

def test_resume_skips_earlier_stages(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    called = []
    monkeypatch.setattr(orch, "STAGES",
        [(n, lambda m, x=n: called.append(x)) for n in ("normalize", "diarize", "transcribe")])
    orch.run_pipeline(mid, from_stage="diarize")
    assert called == ["diarize", "transcribe"]

def test_extraction_failure_does_not_block_publish(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    def boom(_): raise RuntimeError("llm down")
    monkeypatch.setattr(orch, "STAGES",
        [("key_topics", boom), ("minutes", lambda m: None), ("publish", orch.publish)])
    orch.run_pipeline(mid)
    with get_session() as s:
        m = s.get(Meeting, mid)
        assert m.status == "published" and m.failed_stage == "key_topics"

def test_pipeline_publishes_transitions_and_clears_current_stage(
        monkeypatch, make_meeting):
    from meeting_mgr.models import Meeting
    events = []
    monkeypatch.setattr(orch, "publish_progress",
                        lambda mid, stage, state: events.append((stage, state)))
    monkeypatch.setattr(orch, "STAGES", [("normalize", lambda m: None)])
    mid = make_meeting(b"RIFFfake")
    orch.run_pipeline(mid)
    assert ("normalize", "started") in events
    assert ("normalize", "finished") in events
    with get_session() as s:
        assert s.get(Meeting, mid).current_stage is None, "must not stay mid-stage"

def test_the_publish_stage_still_exists_after_the_import(make_meeting):
    # Guards the collision above: if progress.publish shadowed the stage
    # function, STAGES' last entry would no longer mark meetings published.
    from meeting_mgr.models import Meeting
    mid = make_meeting(b"RIFFfake")
    orch.publish(mid)
    with get_session() as s:
        assert s.get(Meeting, mid).status == "published"
