"""Health and readiness probe tests.

The distinction under test: liveness must never fail because a dependency is
down (that would cause an orchestrator restart loop), while readiness must fail
so the instance is removed from the load balancer.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from coursellm.api.routers import health

pytestmark = pytest.mark.unit


async def test_healthz_reports_service_identity(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"]
    assert body["environment"] == "local"
    assert body["retrieval_config_version"]


async def test_healthz_exposes_the_retrieval_config_version(client: AsyncClient) -> None:
    """Support needs to know which retrieval configuration an instance is running."""
    body = (await client.get("/healthz")).json()
    assert len(body["retrieval_config_version"]) == 12


async def test_readyz_is_ready_with_no_registered_dependencies(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == []


async def test_readyz_passes_when_all_checks_pass(client: AsyncClient) -> None:
    async def ok() -> None:
        return None

    health.register_check("database", ok)
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"][0]["name"] == "database"
    assert body["checks"][0]["ok"] is True


async def test_readyz_returns_503_when_a_critical_check_fails(client: AsyncClient) -> None:
    async def broken() -> None:
        raise ConnectionError("connection refused")

    health.register_check("database", broken, critical=True)
    response = await client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"][0]["ok"] is False
    assert "ConnectionError" in (body["checks"][0]["detail"] or "")


async def test_readyz_stays_ready_when_only_a_non_critical_check_fails(
    client: AsyncClient,
) -> None:
    """Redis being down degrades performance; it must not remove the instance."""

    async def broken() -> None:
        raise ConnectionError("redis unavailable")

    health.register_check("redis", broken, critical=False)
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_slow_check_times_out_rather_than_hanging_the_probe(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_PROBE_TIMEOUT_SECONDS", 0.01)

    async def slow() -> None:
        await asyncio.sleep(5)

    health.register_check("database", slow)
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert "timed out" in (response.json()["checks"][0]["detail"] or "")


async def test_checks_run_concurrently(client: AsyncClient) -> None:
    """Probes are independent; running them in series would multiply latency."""

    async def slow() -> None:
        await asyncio.sleep(0.05)

    health.register_check("a", slow)
    health.register_check("b", slow)
    health.register_check("c", slow)

    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["duration_ms"] < 140, "probes appear to run sequentially"


async def test_registered_checks_helper() -> None:
    async def ok() -> None:
        return None

    health.clear_checks()
    assert health.registered_checks() == []
    health.register_check("database", ok)
    assert health.registered_checks() == ["database"]
    health.clear_checks()
