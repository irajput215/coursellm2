"""``GET /metrics``: the Prometheus scrape endpoint.

Deliberately unauthenticated. A scraper has no application token and forcing one
would either break scraping or push a shared credential into every monitoring
system. The data is safe to expose for the same structural reason it is safe to
store: the registry's label set is closed and contains no tenant, user, request
or free-text label (see :mod:`coursellm.observability.metrics`), so the endpoint
cannot answer "what did tenant X do" even if an operator wanted it to.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from coursellm.api.deps import SettingsDep
from coursellm.observability.metrics import get_registry

router = APIRouter(tags=["metrics"])

#: Prometheus text exposition format, version 0.0.4. The charset is made
#: explicit because a scraper treats the body as text and a mismatch is a
#: parse error rather than a warning.
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get(
    "/metrics",
    summary="Prometheus metrics",
    description=(
        "The process metrics registry in Prometheus text format. No authentication "
        "is required and no tenant-scoped label is ever emitted."
    ),
    response_class=Response,
    responses={
        200: {"content": {PROMETHEUS_CONTENT_TYPE: {}}},
        404: {"description": "Metrics collection is disabled for this deployment."},
    },
)
async def metrics(settings: SettingsDep) -> Response:
    if not settings.metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics are disabled for this deployment.",
        )
    return Response(content=get_registry().render(), media_type=PROMETHEUS_CONTENT_TYPE)


__all__ = ["PROMETHEUS_CONTENT_TYPE", "router"]
