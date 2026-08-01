"""Retention policy configuration and purge candidate selection.

select_purge_candidates() (Task 4) is the single source of truth for "what
would this org's policy delete right now" -- both the dry-run preview
endpoint and the real purge task call it unmodified, so a preview can never
disagree with what actually happens.

Provenance is not consulted anywhere in this module. "confirmed" marks a
fact as a human decision, not a hold on deletion -- see the Global
Constraints note in the Phase 4 plan for why, and Task 4/api/retention.py
for how an operator is told what a policy will destroy before it runs.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from meeting_mgr.models import (
    ActionItem,
    Attribution,
    DecisionPoint,
    KeyTopic,
    Meeting,
    Minute,
    Recording,
    RetentionPolicy,
    Segment,
    SpeakerCluster,
)

_ARTIFACT_MODELS = (KeyTopic, Minute, ActionItem, DecisionPoint)


def get_policy(s, org_id: int) -> RetentionPolicy | None:
    return s.query(RetentionPolicy).filter_by(organization_id=org_id).one_or_none()


def upsert_policy(
    s, org_id: int, *, audio_retention_days: int | None, meeting_retention_days: int | None
) -> RetentionPolicy:
    policy = get_policy(s, org_id)
    if policy is None:
        policy = RetentionPolicy(organization_id=org_id)
        s.add(policy)
    policy.audio_retention_days = audio_retention_days
    policy.meeting_retention_days = meeting_retention_days
    s.flush()
    return policy


@dataclass
class PurgeCandidate:
    meeting_id: int
    title: str
    created_at: datetime
    kind: str  # "audio" or "full"
    provenance_counts: dict[str, dict[str, int]]


def _provenance_counts(s: Session, meeting_id: int) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for model in _ARTIFACT_MODELS:
        rows = (
            s.query(model.provenance, func.count(model.id))
            .filter(model.meeting_id == meeting_id)
            .group_by(model.provenance)
            .all()
        )
        counts[model.__tablename__] = {p: n for p, n in rows}
    attribution_rows = (
        s.query(Attribution.provenance, func.count(Attribution.id))
        .join(SpeakerCluster, Attribution.cluster_id == SpeakerCluster.id)
        .filter(SpeakerCluster.meeting_id == meeting_id)
        .group_by(Attribution.provenance)
        .all()
    )
    counts["attribution"] = {p: n for p, n in attribution_rows}
    segment_total = (
        s.query(func.count(Segment.id)).filter(Segment.meeting_id == meeting_id).scalar() or 0
    )
    counts["segment"] = {"total": segment_total}
    return counts


def select_purge_candidates(
    s: Session, org_id: int, *, now: datetime | None = None, limit: int | None = 500
) -> list[PurgeCandidate]:
    """The single source of truth for "what would this org's retention
    policy delete right now" -- see module docstring. `now` is injectable so
    tests can simulate age without waiting real days or backdating rows past
    what a fresh INSERT would otherwise assign.

    `limit` bounds how many rows *of each kind* ("full" and "audio" counted
    separately) this call returns, oldest Meeting first. Default 500: each
    candidate costs six provenance-count queries (`_provenance_counts()`),
    so an unbounded batch on an org whose policy was just tightened -- or
    that has a long backlog -- would make a single purge_organization
    Celery task run for an unpredictable, potentially very long time,
    holding a worker and widening the blast radius of anything that goes
    wrong mid-run. 500 keeps one invocation a bounded, predictable unit of
    work; sweep_retention runs daily, so a backlog bigger than 500 simply
    drains over several days instead of one long run -- strictly better for
    a destructive operation (smaller transactions, visible progress between
    batches).

    Passing limit=None -- what the dry-run preview (Task 5) does -- removes
    the cap and returns the entire backlog. This is deliberate: the preview
    and purge_organization call the identical query and filters, so a
    preview can never disagree with what purge_organization will eventually
    delete. They differ only in how much of the result they return, never
    in what counts as eligible.

    A Meeting eligible for a full purge is always excluded from the audio
    branch at the SQL level (not just within one page), using the complete
    set of full-eligible ids regardless of `limit` -- otherwise a Meeting
    old enough for a full purge, but pushed past `limit` on the full page,
    could be wrongly listed as an audio candidate in the same call.
    """
    now = now or datetime.utcnow()
    policy = get_policy(s, org_id)
    if policy is None:
        return []

    candidates: list[PurgeCandidate] = []
    full_ids: set[int] = set()

    if policy.meeting_retention_days is not None:
        cutoff = now - timedelta(days=policy.meeting_retention_days)
        full_query = s.query(Meeting).filter(
            Meeting.organization_id == org_id, Meeting.created_at <= cutoff
        )
        # Unbounded id-only fetch -- cheap, no provenance counting -- so the
        # audio branch below can exclude every full-eligible Meeting, not
        # just the ones that fit on this page.
        full_ids = {row.id for row in full_query.with_entities(Meeting.id).all()}
        full_rows = full_query.order_by(Meeting.created_at.asc())
        if limit is not None:
            full_rows = full_rows.limit(limit)
        for m in full_rows.all():
            candidates.append(
                PurgeCandidate(m.id, m.title, m.created_at, "full", _provenance_counts(s, m.id))
            )

    if policy.audio_retention_days is not None:
        cutoff = now - timedelta(days=policy.audio_retention_days)
        audio_query = (
            s.query(Meeting)
            .join(Recording, Recording.meeting_id == Meeting.id)
            .filter(Meeting.organization_id == org_id, Meeting.created_at <= cutoff)
            .filter(Recording.raw_key.isnot(None))
        )
        if full_ids:
            # A Meeting past both thresholds is purged fully; listing it a
            # second time here would double-count in the dry-run preview
            # and double-audit in the real run. Excluded in SQL (not with a
            # Python `if m.id in full_ids: continue`) so the exclusion
            # happens before `limit`, not after.
            audio_query = audio_query.filter(Meeting.id.notin_(full_ids))
        audio_query = audio_query.order_by(Meeting.created_at.asc())
        if limit is not None:
            audio_query = audio_query.limit(limit)
        for m in audio_query.all():
            candidates.append(
                PurgeCandidate(m.id, m.title, m.created_at, "audio", _provenance_counts(s, m.id))
            )

    return candidates
