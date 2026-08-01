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

from meeting_mgr.models import RetentionPolicy


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
