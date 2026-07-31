from meeting_mgr.db import get_session
from meeting_mgr.models import Segment, SpeakerCluster
from meeting_mgr.pipeline.align import align, overlap

def test_overlap_is_never_negative():
    assert overlap(0, 1, 5, 6) == 0.0
    assert overlap(0, 3, 2, 5) == 1.0

def test_align_assigns_best_overlapping_cluster(make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        a = SpeakerCluster(meeting_id=mid, label="SPEAKER_00",
                           spans=[{"start": 0.0, "end": 2.5}])
        b = SpeakerCluster(meeting_id=mid, label="SPEAKER_01",
                           spans=[{"start": 2.5, "end": 6.0}])
        s.add_all([a, b]); s.flush()
        s.add_all([
            Segment(meeting_id=mid, start_seconds=0.0, end_seconds=2.0, text="hi"),
            Segment(meeting_id=mid, start_seconds=3.0, end_seconds=5.0, text="yes"),
            Segment(meeting_id=mid, start_seconds=90.0, end_seconds=92.0, text="orphan"),
        ])
        a_id, b_id = a.id, b.id
    align(mid)
    with get_session() as s:
        segs = {x.text: x.cluster_id
                for x in s.query(Segment).filter_by(meeting_id=mid).all()}
    assert segs["hi"] == a_id
    assert segs["yes"] == b_id
    assert segs["orphan"] is None
