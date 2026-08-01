from meeting_mgr.db import get_session
from meeting_mgr.models import Attribution, Participant, Segment, SpeakerCluster
from meeting_mgr.pipeline.attribute import AttributionProposal, attribute, render_transcript


def _seed(mid):
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        s.add(
            Segment(
                meeting_id=mid,
                cluster_id=c.id,
                start_seconds=0.0,
                end_seconds=1.0,
                text="thanks Sarah",
            )
        )


def test_render_transcript_labels_speakers(make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    assert render_transcript(mid) == "[SPEAKER_00] thanks Sarah"


def test_attribute_creates_inferred_attribution(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": "Sarah"}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        a = s.query(Attribution).join(SpeakerCluster).filter(SpeakerCluster.meeting_id == mid).one()
        assert a.provenance == "inferred"
        assert s.get(Participant, a.participant_id).name == "Sarah"


def test_attribute_skips_unnamed_clusters(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": None}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        assert (
            s.query(Attribution)
            .join(SpeakerCluster)
            .filter(SpeakerCluster.meeting_id == mid)
            .count()
            == 0
        )


def test_attribute_skips_whitespace_only_names(monkeypatch, make_meeting):
    mid = make_meeting(b"RIFFfake")
    _seed(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {"names": [{"label": "SPEAKER_00", "name": "   "}]}
        ),
    )
    attribute(mid)
    with get_session() as s:
        assert (
            s.query(Attribution)
            .join(SpeakerCluster)
            .filter(SpeakerCluster.meeting_id == mid)
            .count()
        ) == 0, "a whitespace-only name identifies nobody and must not be attributed"


def _seed_simultaneous(mid):
    """Two segments at the SAME start_seconds, then rewrite the earlier one.

    The UPDATE is the point. Postgres MVCC writes a new tuple version at
    the end of the heap, so a sequential scan now returns the second-
    inserted row first. Without an explicit tiebreak the query therefore
    returns them in the wrong order -- which is what makes this test able
    to fail. Seeding two rows and asserting insertion order would pass
    with or without the ORDER BY, proving nothing.
    """
    with get_session() as s:
        c = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        s.add(c)
        s.flush()
        first = Segment(
            meeting_id=mid, cluster_id=c.id, start_seconds=1.0, end_seconds=2.0, text="placeholder"
        )
        second = Segment(
            meeting_id=mid, cluster_id=c.id, start_seconds=1.0, end_seconds=2.0, text="second"
        )
        s.add_all([first, second])
        s.flush()
        # Must be a REAL value change: assigning the value the row already
        # holds leaves it clean, SQLAlchemy issues no UPDATE, and the heap
        # is never rewritten -- which silently makes this test vacuous.
        first.text = "first"
        s.flush()
        return first.id, second.id


def test_render_transcript_breaks_start_seconds_ties_on_segment_id(make_meeting):
    mid = make_meeting(b"RIFFfake")
    first_id, second_id = _seed_simultaneous(mid)
    assert first_id < second_id
    # Dropping `Segment.id` from render_transcript's order_by turns this red.
    assert render_transcript(mid) == "[SPEAKER_00] first\n[SPEAKER_00] second"


def _seed_two_clusters(mid):
    with get_session() as s:
        c1 = SpeakerCluster(meeting_id=mid, label="SPEAKER_00", spans=[])
        c2 = SpeakerCluster(meeting_id=mid, label="SPEAKER_01", spans=[])
        s.add_all([c1, c2])
        s.flush()
        s.add_all(
            [
                Segment(
                    meeting_id=mid,
                    cluster_id=c1.id,
                    start_seconds=0.0,
                    end_seconds=1.0,
                    text="hi I'm Sarah",
                ),
                Segment(
                    meeting_id=mid,
                    cluster_id=c2.id,
                    start_seconds=1.0,
                    end_seconds=2.0,
                    text="also Sarah here",
                ),
            ]
        )


def test_attribute_dedups_two_clusters_resolving_to_the_same_name(monkeypatch, make_meeting):
    """Two distinct SpeakerClusters both proposed as "Sarah" -- in the SAME
    organization -- must resolve to ONE Participant row, not two. Both
    clusters live in the same (default) org here on purpose: a test that
    used two different orgs would pass even if dedup were broken, because
    each org would get its own "Sarah" regardless. Keeping both clusters in
    one org means resolve_participant's own lookup-before-insert is what
    decides the outcome.
    """
    mid = make_meeting(b"RIFFfake")
    _seed_two_clusters(mid)
    monkeypatch.setattr(
        "meeting_mgr.pipeline.attribute.structured_chat",
        lambda prompt, schema, **kw: AttributionProposal.model_validate(
            {
                "names": [
                    {"label": "SPEAKER_00", "name": "Sarah"},
                    {"label": "SPEAKER_01", "name": "Sarah"},
                ]
            }
        ),
    )
    attribute(mid)
    with get_session() as s:
        attributions = (
            s.query(Attribution).join(SpeakerCluster).filter(SpeakerCluster.meeting_id == mid).all()
        )
        assert len(attributions) == 2
        participant_ids = {a.participant_id for a in attributions}
        assert len(participant_ids) == 1, "both clusters must dedup to a single Participant"


def test_resolve_participant_dedup_is_scoped_per_organization(make_meeting):
    """The same proposed name in a DIFFERENT organization must not reuse the
    other org's Participant row -- dedup is scoped by organization_id, not
    global on name alone.
    """
    import uuid

    from meeting_mgr.models import Organization
    from meeting_mgr.participants import resolve_participant

    with get_session() as s:
        org_a = s.query(Organization).filter_by(name="default").one()
        org_b = Organization(name=f"dedup-test-{uuid.uuid4()}")
        s.add(org_b)
        s.flush()
        id_a = resolve_participant(s, org_a.id, "Sarah")
        id_b = resolve_participant(s, org_b.id, "Sarah")
        assert id_a != id_b, "Participant dedup must not cross organization boundaries"
