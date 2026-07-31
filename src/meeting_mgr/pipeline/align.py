from meeting_mgr.db import get_session
from meeting_mgr.models import Segment, SpeakerCluster

def overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))

def align(meeting_id: int) -> None:
    with get_session() as s:
        clusters = (s.query(SpeakerCluster)
                     .filter_by(meeting_id=meeting_id)
                     .order_by(SpeakerCluster.id)
                     .all())
        spans = [(c.id, c.spans or []) for c in clusters]
        for seg in s.query(Segment).filter_by(meeting_id=meeting_id).all():
            best_id, best = None, 0.0
            for cluster_id, cluster_spans in spans:
                total = sum(
                    overlap(seg.start_seconds, seg.end_seconds,
                            sp["start"], sp["end"])
                    for sp in cluster_spans
                )
                if total > best:
                    best_id, best = cluster_id, total
            seg.cluster_id = best_id
