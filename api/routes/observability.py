from fastapi import APIRouter, HTTPException

from api.routes.auth import CurrentUserDep
from core.config import settings
from observability.event_store import get_events, get_owner_user_id
from schemas.observability import PipelineEventsResponse

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/events/{request_id}", response_model=PipelineEventsResponse)
def get_pipeline_events(request_id: str, current_user: CurrentUserDep):
    """
    Return structured pipeline events for a request (dev/debug).

    Use the X-Request-ID from a prior /ask call. Requires JWT; only the owning user can read events.
    """
    if not settings.OBSERVABILITY_DEBUG_API_ENABLED:
        raise HTTPException(
            status_code=404,
            detail="Observability debug API is disabled",
        )

    owner_id = get_owner_user_id(request_id)
    events = get_events(request_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"No events found for request_id '{request_id}'")

    if owner_id is not None and owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed to view events for this request")

    return PipelineEventsResponse(
        request_id=request_id,
        events=events,
        count=len(events),
    )
