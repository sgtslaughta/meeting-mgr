from meeting_mgr.db import get_session
from meeting_mgr.models import Segment, SpeakerCluster
from meeting_mgr.pipeline.align import align, overlap


def test_overlap_is_never_negative():
    assert overlap(0, 1, 5, 6) == 0.0
    assert overlap(0, 3, 2, 5) == 1.0


def test_align_assigns_best_overlapping_cluster(make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        a = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[{"start": 0.0, "end": 2.5}])
        b = SpeakerCluster(meeting_id=mid, label="SPEAKER_01", spans=[{"start": 2.5, "end": 6.0}])
        s.add_all([a, b])
        s.flush()
        s.add_all(
            [
                Segment(meeting_id=mid, start_seconds=0.0, end_seconds=2.0, text="hi"),
                Segment(meeting_id=mid, start_seconds=3.0, end_seconds=5.0, text="yes"),
                Segment(meeting_id=mid, start_seconds=90.0, end_seconds=92.0, text="orphan"),
            ]
        )
        a_id, b_id = a.id, b.id
    align(mid)
    with get_session() as s:
        segs = {x.text: x.cluster_id for x in s.query(Segment).filter_by(meeting_id=mid).all()}
    assert segs["hi"] == a_id
    assert segs["yes"] == b_id
    assert segs["orphan"] is None


def test_align_sums_overlap_across_disjoint_spans(make_meeting):
    """A cluster's total overlap is the SUM of all its spans, not any single one.

    Cluster A has two spans, each individually overlapping the segment less
    than cluster B's single span -- but summed together they beat B. If the
    scoring only looked at the best single span (or the first span) per
    cluster, B would win instead.
    """
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        a = SpeakerCluster(
            meeting_id=mid,
            label="SPEAKER_00",
            spans=[{"start": 0.0, "end": 3.0}, {"start": 7.0, "end": 10.0}],
        )
        b = SpeakerCluster(meeting_id=mid, label="SPEAKER_01", spans=[{"start": 3.0, "end": 7.0}])
        s.add_all([a, b])
        s.flush()
        s.add(Segment(meeting_id=mid, start_seconds=0.0, end_seconds=10.0, text="wide"))
        a_id = a.id
    align(mid)
    with get_session() as s:
        seg = s.query(Segment).filter_by(meeting_id=mid, text="wide").one()
        assert seg.cluster_id == a_id


def test_align_never_assigns_a_cluster_with_empty_spans(make_meeting):
    """A cluster with an empty spans list contributes zero overlap and must
    never be chosen -- neither over a real match, nor (when it's the only
    cluster) by default. It also must not raise (division-by-zero /
    index error) when its span list is iterated.
    """
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        empty = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        real = SpeakerCluster(
            meeting_id=mid, label="SPEAKER_01", spans=[{"start": 0.0, "end": 2.0}]
        )
        s.add_all([empty, real])
        s.flush()
        s.add(Segment(meeting_id=mid, start_seconds=0.0, end_seconds=2.0, text="matched"))
        s.add(Segment(meeting_id=mid, start_seconds=50.0, end_seconds=52.0, text="unmatched"))
        real_id = real.id
    align(mid)
    with get_session() as s:
        segs = {x.text: x.cluster_id for x in s.query(Segment).filter_by(meeting_id=mid).all()}
    assert segs["matched"] == real_id
    assert segs["unmatched"] is None, "an empty-spans cluster must never win a zero-overlap default"


def test_align_breaks_an_exact_tie_deterministically(make_meeting):
    mid = make_meeting(b"RIFFfake")
    with get_session() as s:
        a = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[{"start": 0.0, "end": 1.0}])
        b = SpeakerCluster(meeting_id=mid, label="SPEAKER_01", spans=[{"start": 1.0, "end": 2.0}])
        s.add_all([a, b])
        s.flush()
        # Straddles both clusters with exactly 0.5s of overlap each.
        s.add(Segment(meeting_id=mid, start_seconds=0.5, end_seconds=1.5, text="tied"))
        lower_id = min(a.id, b.id)
    for _ in range(3):
        align(mid)
        with get_session() as s:
            seg = s.query(Segment).filter_by(meeting_id=mid, text="tied").one()
            assert seg.cluster_id == lower_id, "an exact tie must resolve the same way every run"
