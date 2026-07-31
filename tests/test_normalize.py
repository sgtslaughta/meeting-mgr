import pytest, subprocess
from meeting_mgr.db import get_session
from meeting_mgr.models import Recording
from meeting_mgr.pipeline.normalize import normalize, NormalizeError
from meeting_mgr.storage import get_object

def test_normalize_produces_16k_mono_wav(tmp_path, make_meeting):
    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(src)], check=True, capture_output=True)
    mid = make_meeting(src.read_bytes())
    normalize(mid)
    out = get_object(f"normalized/{mid}.wav")
    assert out[:4] == b"RIFF"
    with get_session() as s:
        rec = s.query(Recording).filter_by(meeting_id=mid).one()
        assert rec.normalized_key == f"normalized/{mid}.wav"
        assert 0.9 < rec.duration_seconds < 1.1

def test_normalize_rejects_corrupt_input(make_meeting):
    mid = make_meeting(b"not audio at all", name="bad.wav")
    with pytest.raises(NormalizeError):
        normalize(mid)

def test_normalize_never_buffers_whole_files(monkeypatch, tmp_path, make_meeting):
    from meeting_mgr.pipeline import normalize as mod
    def boom(*a, **kw):
        raise AssertionError("normalize must stream, not buffer whole files")
    monkeypatch.setattr(mod, "get_stream", mod.get_stream)  # keep the real one
    monkeypatch.setattr("meeting_mgr.storage.get_object", boom)
    monkeypatch.setattr("meeting_mgr.storage.put_object", boom)

    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(src)],
                   check=True, capture_output=True)
    mid = make_meeting(src.read_bytes())
    normalize(mid)   # must not touch the buffering helpers


def test_normalize_raises_normalize_error_on_bad_ffprobe_output(
        monkeypatch, tmp_path, make_meeting):
    import subprocess as sp
    from meeting_mgr.pipeline import normalize as mod

    src = tmp_path / "tone.wav"
    sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=1", str(src)],
           check=True, capture_output=True)
    mid = make_meeting(src.read_bytes())

    real_run = mod.subprocess.run
    def fake_run(cmd, *a, **kw):
        result = real_run(cmd, *a, **kw)
        if cmd[0] == "ffprobe":
            result.stdout = "N/A\n"      # ffprobe's real output for unknown duration
        return result
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    with pytest.raises(NormalizeError):
        normalize(mid)
