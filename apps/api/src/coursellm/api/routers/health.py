"""Health and readiness endpoints.

Two distinct probes, because they answer different questions and a deployment
orchestrator must act differently on each:

``GET /healthz``  Liveness. "Is this process alive?" It must not touch the
                  database, Redis or any provider, because a failure here
                  causes the orchestrator to *restart* the container. A slow
                  dependency must never trigger a restart loop.

``GET /readyz``   Readiness. "Should this instance receive traffic?" It checks
                  each registered dependency and returns 503 when any is
                  critical and down, which removes the instance from the load
                  balancer without killing it.

Dependency checks are registered rather than hard-coded so that later layers add
their own probe without this module importing them.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from coursellm.api.deps import SettingsDep
from coursellm.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

_PROBE_TIMEOUT_SECONDS = 3.0


@dataclass(slots=True)
class DependencyCheck:
    """A single readiness probe."""

    name: str
    check: Callable[[], Awaitable[None]]
    critical: bool = True
    detail: str = ""


_checks: list[DependencyCheck] = []


def register_check(
    name: str,
    check: Callable[[], Awaitable[None]],
    *,
    critical: bool = True,
    detail: str = "",
) -> None:
    """Register a readiness probe. Later layers call this at import time."""
    _checks.append(DependencyCheck(name=name, check=check, critical=critical, detail=detail))


def clear_checks() -> None:
    """Remove all probes. Used by tests to isolate readiness behaviour."""
    _checks.clear()


def registered_checks() -> list[str]:
    return [c.name for c in _checks]


class HealthResponse(BaseModel):
    status: str = Field(description="Always 'ok' when the process is alive.")
    service: str
    version: str
    environment: str
    retrieval_config_version: str = Field(
        description=(
            "Hash of retrieval-affecting configuration; changes when retrieval behaviour changes."
        ),
    )


class CheckResult(BaseModel):
    name: str
    ok: bool
    critical: bool
    latency_ms: float
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: str = Field(description="'ready' when every critical check passed, else 'degraded'.")
    checks: list[CheckResult]
    duration_ms: float


@router.get(
    "/healthz",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Cheap, dependency-free. Never performs I/O.",
)
async def healthz(settings: SettingsDep) -> HealthResponse:
    from coursellm import __version__

    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        retrieval_config_version=settings.retrieval_config_version,
    )


async def _run_check(check: DependencyCheck) -> CheckResult:
    started = time.perf_counter()
    ok: bool
    detail: str | None
    try:
        await asyncio.wait_for(check.check(), timeout=_PROBE_TIMEOUT_SECONDS)
    except TimeoutError:
        ok, detail = False, f"timed out after {_PROBE_TIMEOUT_SECONDS:.0f}s"
    except Exception as exc:
        ok, detail = False, f"{type(exc).__name__}: {exc}"
        logger.warning("readiness_check_failed", check=check.name, error=detail)
    else:
        ok, detail = True, (check.detail or None)
    return CheckResult(
        name=check.name,
        ok=ok,
        critical=check.critical,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        detail=detail,
    )


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Runs every registered dependency probe. Returns 503 when a critical one fails.",
    responses={503: {"description": "At least one critical dependency is unavailable."}},
)
async def readyz(response: Response) -> ReadinessResponse:
    started = time.perf_counter()
    if not _checks:
        # No dependencies registered yet. This is the state during early
        # bring-up and in unit tests, and it is honest: the service itself is
        # up, and there is nothing to be unready about.
        return ReadinessResponse(
            status="ready",
            checks=[],
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    results = await asyncio.gather(*(_run_check(c) for c in _checks))
    failed_critical = [r for r in results if r.critical and not r.ok]
    if failed_critical:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    payload: dict[str, Any] = {
        "status": "degraded" if failed_critical else "ready",
        "checks": [r.model_dump() for r in results],
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return ReadinessResponse(**payload)
