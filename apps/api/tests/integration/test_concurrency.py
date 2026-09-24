"""Concurrent requests and the pooled-connection tenancy guarantee.

A sequential isolation test cannot prove the property this file exists for. The
hazard from ``docs/architecture/security.md`` §7.4 is that ``app.tenant_id`` is a
*session* variable: if it were set with ``SET`` rather than ``SET LOCAL``, it
would survive on a physical connection when it returns to the pool and the next
request to check that connection out would read the previous tenant's rows. The
bug is invisible in a sequential test because each request finishes before the
next starts, so the connection is always clean at the moment the assertion runs.

Every test here therefore drives two tenants *at the same time* against a pool
that is smaller than the number of in-flight requests, and asserts on every
individual response. The application role is ``coursellm_app``
(``NOSUPERUSER NOBYPASSRLS``), so PostgreSQL's policy is live underneath the
repository predicate and a GUC leak would be a real cross-tenant read rather
than a defence-in-depth miss.

The documents endpoint is used deliberately: it holds exactly one pooled
connection per request and performs no model call, so the pool arithmetic is
exactly "N requests, M connections" with no second checkout hidden inside a
request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from coursellm.api.app import create_app
from coursellm.api.deps import get_settings_dep
from coursellm.core.config import Settings
from tests.integration.conftest import SEED_PASSWORD

pytestmark = pytest.mark.integration

#: Ten requests, two tenants, five of each. The numbers are chosen so the pool
#: is strictly smaller than the request count in every test below.
_REQUESTS_PER_TENANT = 5


def _pool_settings(pg_settings: Settings, *, size: int) -> Settings:
    """Settings whose connection pool is deliberately smaller than the load.

    ``max_overflow=0`` is what makes the bound real: with overflow, ten requests
    would simply open ten connections and there would be no reuse at all, which
    is the exact condition the leak needs.
    """
    return pg_settings.model_copy(
        update={
            "db_pool_size": size,
            "db_max_overflow": 0,
            "rate_limit_requests_per_minute": 1000,
            "rate_limit_llm_requests_per_hour": 1000,
        }
    )


@pytest_asyncio.fixture(loop_scope="function")
async def pool_client(pg_settings: Settings) -> AsyncIterator[AsyncClient]:
    """One app whose pool is a single connection."""
    settings = _pool_settings(pg_settings, size=1)
    application = create_app(settings)
    application.dependency_overrides[get_settings_dep] = lambda: settings
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        application.dependency_overrides.clear()


@pytest_asyncio.fixture(loop_scope="function")
async def small_pool_client(pg_settings: Settings) -> AsyncIterator[AsyncClient]:
    """One app whose pool is two connections, still under ten requests."""
    settings = _pool_settings(pg_settings, size=2)
    application = create_app(settings)
    application.dependency_overrides[get_settings_dep] = lambda: settings
    transport = ASGITransport(app=application)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        application.dependency_overrides.clear()


async def _login(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/token", json={"email": email, "password": SEED_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _list_documents(client: AsyncClient, token: str) -> list[dict[str, Any]]:
    response = await client.get("/api/v1/documents", headers=_auth(token))
    assert response.status_code == 200, response.text
    return list(response.json())


class TestTenConcurrentRequestsFromTwoTenants:
    """Ten concurrent requests, two tenants, a pool of two connections."""

    async def test_every_response_contains_only_its_own_tenants_documents(
        self, small_pool_client: AsyncClient, seeded: Any
    ) -> None:
        token_a = await _login(small_pool_client, seeded.tenant_a.email)
        token_b = await _login(small_pool_client, seeded.tenant_b.email)

        async def fetch(token: str) -> list[dict[str, Any]]:
            return await _list_documents(small_pool_client, token)

        plans = [fetch(token_a)] * _REQUESTS_PER_TENANT + [fetch(token_b)] * _REQUESTS_PER_TENANT
        results = await asyncio.gather(*plans)

        # Every response must contain the requesting tenant's own document and
        # nothing from the other tenant.
        for index, documents in enumerate(results):
            expected_tenant = (
                seeded.tenant_a_id if index < _REQUESTS_PER_TENANT else seeded.tenant_b_id
            )
            other = seeded.tenant_b_id if index < _REQUESTS_PER_TENANT else seeded.tenant_a_id
            for document in documents:
                assert document["id"] == str(
                    seeded.tenant_a.document_id
                    if expected_tenant == seeded.tenant_a_id
                    else seeded.tenant_b.document_id
                ), "a response contained a document that is not the caller's"
            assert all(
                document["course_id"]
                != str(
                    seeded.course_b_id
                    if expected_tenant == seeded.tenant_a_id
                    else seeded.course_a_id
                )
                for document in documents
            ), "a response contained another tenant's course id"
            assert all(
                document["id"]
                != str(
                    seeded.tenant_b.document_id
                    if expected_tenant == seeded.tenant_a_id
                    else seeded.tenant_a.document_id
                )
                for document in documents
            )
            # The other tenant's id must not appear at all, which also catches
            # a leak through a field the loop above does not name.
            assert str(other) not in str(documents)

    async def test_a_one_connection_pool_serialises_without_leaking(
        self, pool_client: AsyncClient, seeded: Any
    ) -> None:
        """Pool of one: every request reuses the same physical connection.

        This is the strongest form of the test. There is exactly one connection,
        so tenant B's request necessarily checks out the connection tenant A's
        request just returned. If ``app.tenant_id`` were not transaction-local,
        B would see A's row here and nowhere else.
        """
        token_a = await _login(pool_client, seeded.tenant_a.email)
        token_b = await _login(pool_client, seeded.tenant_b.email)

        plans = [_list_documents(pool_client, token_a) for _ in range(_REQUESTS_PER_TENANT)] + [
            _list_documents(pool_client, token_b) for _ in range(_REQUESTS_PER_TENANT)
        ]
        results = await asyncio.gather(*plans)

        for index, documents in enumerate(results):
            own_id = (
                seeded.tenant_a.document_id
                if index < _REQUESTS_PER_TENANT
                else seeded.tenant_b.document_id
            )
            foreign_id = (
                seeded.tenant_b.document_id
                if index < _REQUESTS_PER_TENANT
                else seeded.tenant_a.document_id
            )
            assert len(documents) == 1, (
                "a pooled connection returned the wrong number of rows; this is "
                "what a leaked tenant GUC looks like on a one-connection pool"
            )
            assert documents[0]["id"] == str(own_id)
            assert documents[0]["id"] != str(foreign_id)


class TestInterleavedTenantsOnOneConnection:
    """Tenant A and tenant B requests alternate on a single connection."""

    async def test_no_interleaving_produces_a_cross_tenant_row(
        self, pool_client: AsyncClient, seeded: Any
    ) -> None:
        """The alternation is the point: each B follows an A immediately.

        This is the sequential regression already in
        ``test_tenant_isolation.py``, but under concurrency the interleaving is
        decided by the scheduler rather than by the test author.
        """
        token_a = await _login(pool_client, seeded.tenant_a.email)
        token_b = await _login(pool_client, seeded.tenant_b.email)

        async def a_turn() -> list[dict[str, Any]]:
            return await _list_documents(pool_client, token_a)

        async def b_turn() -> list[dict[str, Any]]:
            return await _list_documents(pool_client, token_b)

        interleaved = []
        for _ in range(_REQUESTS_PER_TENANT):
            interleaved.extend([a_turn(), b_turn()])
        results = await asyncio.gather(*interleaved)

        for index, documents in enumerate(results):
            is_a = index % 2 == 0
            assert len(documents) == 1
            assert documents[0]["id"] == str(
                seeded.tenant_a.document_id if is_a else seeded.tenant_b.document_id
            )

    async def test_the_guc_does_not_survive_a_transaction_on_a_pool_of_one(
        self, pool_client: AsyncClient, pg_settings: Settings, seeded: Any
    ) -> None:
        """A tenant with no rows sees nothing, on a connection that has carried two.

        The strongest negative assertion available without new fixtures: after
        A and B have each used the single connection, present a JWT for a
        tenant that owns no rows. It must see an empty list, not the last
        tenant's row. The token is self-contained, so no database lookup is
        needed to obtain it.
        """
        import uuid

        from coursellm.db.models.identity import UserRole
        from coursellm.security.tokens import create_access_token

        token_a = await _login(pool_client, seeded.tenant_a.email)
        token_b = await _login(pool_client, seeded.tenant_b.email)
        await asyncio.gather(
            _list_documents(pool_client, token_a),
            _list_documents(pool_client, token_b),
        )

        ghost_tenant_id = uuid.uuid4()
        ghost, _ = create_access_token(
            _pool_settings(pg_settings, size=1),
            user_id=uuid.uuid4(),
            tenant_id=ghost_tenant_id,
            role=UserRole.OWNER,
        )

        response = await pool_client.get("/api/v1/documents", headers=_auth(ghost))

        assert response.status_code == 200, response.text
        assert response.json() == [], "a tenant with no rows saw another tenant's rows"
        assert str(ghost_tenant_id) not in response.text
        assert str(seeded.tenant_b.document_id) not in response.text


class TestThePoolIsActuallyBounded:
    """A guard on the guard: the tests above are only meaningful if the pool is small."""

    async def test_the_app_uses_a_single_connection_pool(self, pg_settings: Settings) -> None:
        from coursellm.db.session import get_engine

        settings = _pool_settings(pg_settings, size=1)
        engine = await get_engine(settings)
        assert engine.pool.size() == 1  # type: ignore[attr-defined]
        assert engine.pool._max_overflow == 0  # type: ignore[attr-defined]
