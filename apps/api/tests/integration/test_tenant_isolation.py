"""Row-Level Security and repository-scoping guarantees.

These are the load-bearing tests of the tenancy design. Every test that claims to
demonstrate isolation connects as the ``coursellm_app`` role, because the schema
owner is a superuser locally and a superuser ignores every policy. A passing test
against the owner role would be evidence of nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from coursellm.core.config import Settings
from coursellm.db.models.identity import User, UserRole
from coursellm.db.session import get_session_factory, rls_enforcement_status
from coursellm.db.tenancy import (
    TenantScope,
    assert_tenant_rows_visible,
    set_tenant_guc,
    tenant_session,
)
from coursellm.repositories.identity import UserRepository

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. The role under test must actually be subject to RLS.
# ---------------------------------------------------------------------------
async def test_app_role_is_subject_to_row_level_security(pg_settings: Settings) -> None:
    """A role that can bypass RLS makes every other test here meaningless."""
    factory = await get_session_factory(pg_settings)
    async with factory() as session:
        status = await rls_enforcement_status(session)

    assert status["enforced"] is True
    assert status["is_superuser"] is False
    assert status["bypasses_rls"] is False


async def test_owner_role_reports_rls_as_not_enforced(owner_engine: AsyncEngine) -> None:
    """The hazard the startup check exists to catch.

    A superuser (or a role with ``BYPASSRLS``) is not filtered by any policy, so
    isolation would rest on application code alone. The status probe has to say
    so explicitly rather than assume enforcement from configuration.
    """
    async with AsyncSession(owner_engine) as session:
        status = await rls_enforcement_status(session)

    assert status["enforced"] is False
    assert status["is_superuser"] is True or status["bypasses_rls"] is True


# ---------------------------------------------------------------------------
# 2. Fail closed.
# ---------------------------------------------------------------------------
async def test_without_tenant_context_no_rows_are_visible(pg_settings: Settings, seeded) -> None:
    """No ``app.tenant_id`` means no tenant, which must mean no rows -- not all."""
    factory = await get_session_factory(pg_settings)
    async with factory() as session:
        visible = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()

    assert visible == 0


# ---------------------------------------------------------------------------
# 3. Reads are filtered to the ambient tenant.
# ---------------------------------------------------------------------------
async def test_tenant_sees_only_its_own_users(pg_settings: Settings, seeded) -> None:
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        visible = set((await session.execute(text("SELECT id FROM users"))).scalars().all())

    assert visible == {seeded.user_a_id}
    assert seeded.user_b_id not in visible


async def test_cross_tenant_read_by_id_returns_nothing(pg_settings: Settings, seeded) -> None:
    """Another tenant's id must be indistinguishable from an id that does not exist."""
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        rows = (
            (
                await session.execute(
                    text("SELECT id FROM users WHERE id = :id"),
                    {"id": str(seeded.user_b_id)},
                )
            )
            .scalars()
            .all()
        )

    assert rows == []


# ---------------------------------------------------------------------------
# 4. Writes cannot cross the boundary.
# ---------------------------------------------------------------------------
async def test_cross_tenant_insert_is_rejected_by_the_database(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    """The ``WITH CHECK`` clause is the backstop if the repository layer is wrong."""
    factory = await get_session_factory(pg_settings)
    async with factory() as session:
        await set_tenant_guc(session, seeded.tenant_a_id)
        with pytest.raises(ProgrammingError) as caught:
            await session.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, hashed_password, role) "
                    "VALUES (:id, :tenant_id, :email, 'x', 'member')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(seeded.tenant_b_id),
                    "email": "cross-tenant-intruder@example.com",
                },
            )
        # The failed statement aborts the transaction; roll back before closing.
        await session.rollback()

    assert "row-level security" in str(caught.value).lower()

    async with owner_engine.connect() as connection:
        written = (
            await connection.execute(
                text("SELECT count(*) FROM users WHERE email = :email"),
                {"email": "cross-tenant-intruder@example.com"},
            )
        ).scalar_one()
    assert written == 0


async def test_cross_tenant_update_affects_no_rows(pg_settings: Settings, seeded) -> None:
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        result = await session.execute(
            text("UPDATE users SET full_name = 'compromised' WHERE id = :id"),
            {"id": str(seeded.user_b_id)},
        )

        assert result.rowcount == 0


# ---------------------------------------------------------------------------
# 5. The repository layer applies the same boundary without relying on RLS.
# ---------------------------------------------------------------------------
async def test_repository_get_returns_none_for_another_tenants_row(
    pg_settings: Settings, seeded
) -> None:
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repository = UserRepository(session, TenantScope(seeded.tenant_a_id))

        assert await repository.get(seeded.user_b_id) is None


async def test_repository_add_cannot_write_into_another_tenant(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    """``add`` reassigns the tenant from the ambient scope, not the caller's value."""
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        repository = UserRepository(session, TenantScope(seeded.tenant_a_id))
        staged = repository.add(
            User(
                tenant_id=seeded.tenant_b_id,
                email="reassigned@example.com",
                hashed_password="x",
                role=UserRole.MEMBER,
            )
        )

        assert staged.tenant_id == seeded.tenant_a_id
        await repository.flush()
        staged_id = staged.id

    async with owner_engine.connect() as connection:
        stored_tenant = (
            await connection.execute(
                text("SELECT tenant_id FROM users WHERE id = :id"),
                {"id": str(staged_id)},
            )
        ).scalar_one()

    assert stored_tenant == seeded.tenant_a_id


async def test_scoped_session_passes_the_active_isolation_probe(
    pg_settings: Settings, seeded
) -> None:
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_a_id)) as session:
        assert await assert_tenant_rows_visible(session, seeded.tenant_a_id) is True


# ---------------------------------------------------------------------------
# 6. The tenancy variable must not survive on a pooled connection.
# ---------------------------------------------------------------------------
async def test_tenant_variable_does_not_leak_across_pooled_connections(
    pg_settings: Settings, seeded
) -> None:
    """A locally-scoped tenant must be gone once its transaction ends.

    ``set_config('app.tenant_id', ..., is_local => true)`` is what makes the
    value transaction-scoped; the repository and request dependencies rely on
    that. This test pins the boundary by reusing the *same* physical connection
    (the pool has one) for a second, unscoped session.

    Regression note: PostgreSQL resets a locally-set custom GUC to the **empty
    string** on commit, not to NULL, so
    ``current_setting('app.tenant_id', true)::uuid`` used to raise ``invalid
    input syntax for type uuid: ""``. The policy now wraps the setting in
    ``NULLIF(..., '')``. The severity of that defect was availability, not
    confidentiality: it would have produced a 500 on every request served by a
    connection that had once carried a tenant, while never exposing another
    tenant's rows. It is kept as a regression test because removing the
    ``NULLIF`` silently reintroduces the 500.
    """
    factory = await get_session_factory(pg_settings)

    # The first session sets the tenant locally and commits, returning its
    # connection to a single-connection pool.
    async with factory() as session, session.begin():
        await set_tenant_guc(session, seeded.tenant_a_id)
        scoped = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert scoped == 1

    # The next session must reuse that same physical connection and see nothing.
    async with factory() as session:
        unscoped = (await session.execute(text("SELECT count(*) FROM users"))).scalar_one()
    assert unscoped == 0
