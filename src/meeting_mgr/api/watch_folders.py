import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from meeting_mgr.audit import record_audit
from meeting_mgr.auth.deps import get_current_account
from meeting_mgr.authz import require_role
from meeting_mgr.db import get_org_session, get_readonly_org_session
from meeting_mgr.models import Account
from meeting_mgr.pipeline.watch_config import SCAN_INTERVAL_SECONDS
from meeting_mgr.watch_folder import list_watch_folders, upsert_watch_folder

router = APIRouter(prefix="/watch-folders")

_ADMIN_ONLY = frozenset({"admin"})
# Two missed scans before an operator is told something looks wrong -- one
# missed run alone could just be a slow beat tick, not a dead watcher.
_STALLED_AFTER = timedelta(seconds=2 * SCAN_INTERVAL_SECONDS)


class WatchFolderIn(BaseModel):
    root_path: str
    owner_account_id: int
    enabled: bool = True


def _view(wf) -> dict:
    reference = wf.last_scan_at or wf.created_at
    stalled = wf.enabled and (datetime.utcnow() - reference) > _STALLED_AFTER
    return {
        "id": wf.id,
        "root_path": wf.root_path,
        "owner_account_id": wf.owner_account_id,
        "enabled": wf.enabled,
        "last_scan_at": wf.last_scan_at,
        "last_scan_error": wf.last_scan_error,
        "stalled": stalled,
    }


@router.get("")
def read_watch_folders(account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    with get_readonly_org_session(account.organization_id) as s:
        return [_view(wf) for wf in list_watch_folders(s, account.organization_id)]


@router.put("")
def write_watch_folder(body: WatchFolderIn, account: Account = Depends(get_current_account)):
    require_role(account, _ADMIN_ONLY)
    # A relative path's meaning depends on the scanner process's working
    # directory, so the same stored value could resolve to a different
    # directory across restarts/deployments -- silently ingesting files
    # nobody registered. Reject at the write boundary; a Postgres CHECK
    # can't see os.path semantics.
    if not os.path.isabs(body.root_path):
        raise HTTPException(422, "root_path must be an absolute path")
    with get_org_session(account.organization_id) as s:
        owner = s.get(Account, body.owner_account_id)
        # Pass 1 established (VERIFIED): under get_org_session, tenant_isolation
        # RLS on `account` already makes s.get() return None for any
        # cross-org id, so `owner is None` alone accounts for every 422 this
        # raises today -- `owner.organization_id != account.organization_id`
        # is currently unreachable dead code. See
        # test_cross_org_owner_lookup_returns_none_under_rls_is_the_active_guard
        # and test_owner_organization_mismatch_comparison_is_correct_if_ever_reached
        # in test_api_watch_folders.py, which isolate the two halves.
        # Kept deliberately anyway: it is the only thing that would still
        # catch a cross-org owner if this endpoint were ever moved off
        # get_org_session onto a non-RLS session (get_session()). Do not
        # delete a guard because it is unreachable today -- that is how the
        # reachable case comes back later unnoticed.
        if owner is None or owner.organization_id != account.organization_id:
            raise HTTPException(422, "owner_account_id must belong to your organization")
        wf = upsert_watch_folder(
            s,
            account.organization_id,
            root_path=body.root_path,
            owner_account_id=body.owner_account_id,
            enabled=body.enabled,
        )
        record_audit(
            s,
            organization_id=account.organization_id,
            actor_account_id=account.id,
            action="watch_folder.update",
            target=f"watch_folder:{wf.id}",
            detail={
                "root_path": body.root_path,
                "owner_account_id": body.owner_account_id,
                "enabled": body.enabled,
            },
        )
        view = _view(wf)
    return view
