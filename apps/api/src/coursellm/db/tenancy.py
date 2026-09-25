"""Tenancy: the request-scoped tenant context and the RLS session variable.

Three layers enforce isolation, and all three are required:

1. **Authentication** derives the tenant from a signed token. A tenant is never
   accepted as a request parameter, so there is nothing for a caller to tamper
   with, and no tool argument can widen it (see ``docs/architecture/security.md``).
2. **The repository layer** refuses to build a query without a tenant scope, so a
   forgotten ``WHERE`` is a type error rather than a data leak.
3. **PostgreSQL Row-Level Security** policies key on the ``app.tenant_id`` session
   variable. This is the layer that still holds if application code is wrong.

## Why the GUC is set per transaction, not per session

``set_config('app.tenant_id', …, is_local => true)`` scopes the value to the
current transaction: it is undone by the next ``COMMIT`` or ``ROLLBACK``
automatically. Using session-level ``SET`` would leave the value on a pooled
connection, and the next request to check that connection out — potentially for a
different tenant — would inherit it. With connection pooling that is not a
hypothetical: it is a cross-tenant data leak whose symptom is another tenant's
rows appearing in a response.

## Why the selected strategy is checked instead of assumed

A superuser, or any role granted ``BYPASSRLS``, ignores every policy without
warning. Since local development connects as a superuser by default, the
enforcement state is queried at startup and reported; in production a role that
can bypass RLS is a startup failure.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import PermissionDeniedError
from coursellm.core.logging import get_logger
from coursellm.db.models.identity import UserRole
from coursellm.db.session import get_session_factory

logger = get_logger(__name__)

# The session variable RLS policies read. Defined once so the policy text in the
# migration, the code that sets it and the tests that assert it cannot drift.
TENANT_GUC = "app.tenant_id"


@dataclass(frozen=True, slots=True)
class TenantScope:
    """A tenant boundary without a user.

    Used by background work that operates on behalf of the tenant rather than a
    person: ingestion, re-indexing, evaluation runs.
    """

    # Typed, and not re-validated at runtime. The only producer is
    # :func:`coursellm.security.tokens.decode_access_token`, which parses the
    # claim with ``uuid.UUID`` and raises for anything malformed, so a raw string
    # cannot reach a policy comparison.
    tenant_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class TenantContext(TenantScope):
    """The authenticated principal for a request."""

    user_id: uuid.UUID
    role: UserRole
    email: str = ""

    def require_role(self, *allowed: UserRole) -> None:
        """Raise unless the principal holds one of ``allowed``.

        A small helper rather than a decorator because role checks in this
        codebase gate administrative actions, which are few and should be
        visible at the call site.
        """
        if self.role not in allowed:
            raise PermissionDeniedError(
                "This action requires a higher role than the current account holds."
            )


async def set_tenant_guc(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Bind the tenancy variable for the current transaction.

    ``is_local => true`` is not optional. See the module docstring.
    """
    await session.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": TENANT_GUC, "value": str(tenant_id)},
    )


@asynccontextmanager
async def tenant_session(settings: Settings, scope: TenantScope) -> AsyncIterator[AsyncSession]:
    """Open a transactional session with the tenancy GUC applied.

    The GUC is set *inside* the transaction and before any other statement, so
    every query in the block — including the first — is subject to RLS.

    The transaction is begun by the first statement rather than by a
    ``session.begin()`` context manager. A chat turn must commit the user
    message *before* it runs generation, so that a hard generation failure rolls
    back only the answer and the question survives; SQLAlchemy refuses further
    statements once the transaction owned by a ``session.begin()`` context
    manager has been committed mid-block. Owning commit/rollback here keeps the
    block atomic on the failure path while allowing an explicit mid-block
    commit. A service that commits re-applies the GUC itself
    (``services.chat.commit_turn``) because ``set_config(..., is_local => true)``
    is transaction-scoped.
    """
    factory = await get_session_factory(settings)
    async with factory() as session:
        try:
            await set_tenant_guc(session, scope.tenant_id)
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def assert_tenant_rows_visible(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Verify that RLS hides every row not belonging to ``tenant_id``.

    Used by a startup check and by the isolation test suite. It is deliberately
    an active probe rather than a configuration read: the question is not "is the
    policy defined" but "does the database actually filter".
    """
    result = await session.execute(
        text("SELECT count(*) AS n FROM users WHERE tenant_id <> :tid"),
        {"tid": str(tenant_id)},
    )
    visible_foreign_rows = int(result.scalar_one())
    return visible_foreign_rows == 0
