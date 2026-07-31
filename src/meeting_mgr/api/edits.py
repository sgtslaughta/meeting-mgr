from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from meeting_mgr.db import get_session
from meeting_mgr.models import Attribution, Meeting, SpeakerCluster
from meeting_mgr.participants import resolve_participant
from meeting_mgr.provenance import confirm

router = APIRouter()

class AttributionIn(BaseModel):
    participant_name: str | None = None

@router.patch("/meetings/{meeting_id}/clusters/{cluster_id}")
def confirm_attribution(meeting_id: int, cluster_id: int, body: AttributionIn):
    with get_session() as s:
        cluster = (s.query(SpeakerCluster)
                    .filter_by(id=cluster_id, meeting_id=meeting_id)
                    .one_or_none())
        if cluster is None:
            raise HTTPException(404, "cluster not found in this meeting")

        org_id = s.get(Meeting, meeting_id).organization_id
        # Replace rather than accumulate: a cluster has one holder.
        s.query(Attribution).filter_by(cluster_id=cluster_id).delete()

        participant_id = resolve_participant(s, org_id, body.participant_name)
        if participant_id is None:
            # No fact survives: the absence of an Attribution row IS "nobody
            # has decided yet". Nothing to confirm, nothing to label.
            return {"cluster_id": cluster_id, "participant_id": None,
                    "participant_name": None, "provenance": None}

        attribution = Attribution(cluster_id=cluster_id,
                                  participant_id=participant_id)
        confirm(attribution)
        s.add(attribution)
        return {"cluster_id": cluster_id, "participant_id": participant_id,
                "participant_name": body.participant_name,
                "provenance": "confirmed"}
