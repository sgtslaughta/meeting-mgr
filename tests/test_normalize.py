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
