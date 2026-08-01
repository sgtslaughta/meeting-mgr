from fastapi import APIRouter, Depends
from pydantic import BaseModel, model_validator

from meeting_mgr.audit import record_audit
from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import require_role
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account
from meeting_mgr.retention import get_policy, upsert_policy

router = APIRouter(prefix="/retention-policy")

_ADMIN_ONLY = frozenset({"admin"})


class RetentionPolicyIn(BaseModel):
    audio_retention_days: int | None = None
    meeting_retention_days: int | None = None

    @model_validator(mode="after")
    def _validate(self):
        # `< 0`, not `<= 0`: 0 ("purge immediately") is a legitimate, distinct
        # value from NULL ("keep forever") -- Task 1's CHECK constraint is
        # `>= 0`, and rejecting 0 here would make that DB allowance
        # permanently unreachable through the API. Only negative values,
        # which would put the purge cutoff in the future, are rejected here
        # with a clean 422 rather than surfacing as a DB IntegrityError/500.
        for name, value in (
            ("audio_retention_days", self.audio_retention_days),
            ("meeting_retention_days", self.meeting_retention_days),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if (
            self.audio_retention_days is not None
            and self.meeting_retention_days is not None
            and self.audio_retention_days > self.meeting_retention_days
        ):
            raise ValueError("audio_retention_days must not exceed meeting_retention_days")
        return self


def _view(policy) -> dict:
    if policy is None:
        return {"audio_retention_days": None, "meeting_retention_days": None}
    return {
        "audio_retention_days": policy.audio_retention_days,
        "meeting_retention_days": policy.meeting_retention_days,
    }


@router.get("")
def read_policy(account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_readonly_org_session(account.organization_id) as s:
        return _view(get_policy(s, account.organization_id))


@router.put("")
def write_policy(body: RetentionPolicyIn, account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_org_session(account.organization_id) as s:
        policy = upsert_policy(
            s,
            account.organization_id,
            audio_retention_days=body.audio_retention_days,
            meeting_retention_days=body.meeting_retention_days,
        )
        # detail carries the new configuration -- two integers, not content --
        # same allowlist-of-shape rule confirm_attribution/edit_artifact use.
        record_audit(
            s,
            organization_id=account.organization_id,
            actor_account_id=account.id,
            action="retention_policy.update",
            target=f"organization:{account.organization_id}",
            detail={
                "audio_retention_days": body.audio_retention_days,
                "meeting_retention_days": body.meeting_retention_days,
            },
        )
        view = _view(policy)
    return view
