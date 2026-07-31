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

def test_normalize_streams_both_ways(monkeypatch, tmp_path, make_meeting):
    from meeting_mgr.pipeline import normalize as mod

    # The buffering helpers must not even be bound in this module's namespace.
    assert not hasattr(mod, "get_object"), "normalize must not import get_object"
    assert not hasattr(mod, "put_object"), "normalize must not import put_object"

    # And the streaming helpers must actually be the ones exercised.
    calls = []
    real_get, real_put = mod.get_stream, mod.put_stream

    def spy_get(key, fileobj):
        calls.append(("get_stream", key))
        return real_get(key, fileobj)

    def spy_put(key, fileobj):
        calls.append(("put_stream", key))
        return real_put(key, fileobj)

    monkeypatch.setattr(mod, "get_stream", spy_get)
    monkeypatch.setattr(mod, "put_stream", spy_put)

    src = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=1", str(src)],
                   check=True, capture_output=True)
    mid = make_meeting(src.read_bytes())
    normalize(mid)

    assert [name for name, _ in calls] == ["get_stream", "put_stream"]
    assert calls[1][1] == f"normalized/{mid}.wav"


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
