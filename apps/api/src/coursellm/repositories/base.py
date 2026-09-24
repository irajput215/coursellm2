"""Repositories: the only sanctioned way to reach tenant-scoped data.

A repository is constructed with a :class:`~coursellm.db.tenancy.TenantScope`
and applies it to every statement it builds. The point is not ceremony; it is
that a query which forgets the tenant becomes a type error rather than a data
leak:

* There is no method that returns an unscoped statement.
* ``get`` and ``list`` cannot be called without the scope, because the scope is a
  constructor argument.
* Row-Level Security in the database is the backstop for the case where this
  layer is nonetheless wrong.

Repositories never commit. Transaction boundaries belong to the caller — the
request dependency or the worker — so that a use case spanning several
repositories commits atomically or not at all.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select, delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.errors import NotFoundError
from coursellm.db.base import Base
from coursellm.db.tenancy import TenantScope


class TenantRepository[ModelT: Base]:
    """Base for repositories over a table carrying ``tenant_id``."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession, scope: TenantScope) -> None:
        self._session = session
        self._scope = scope

    @property
    def tenant_id(self) -> uuid.UUID:
        return self._scope.tenant_id

    # -- statement construction ------------------------------------------
    def _select(self) -> Select[tuple[ModelT]]:
        """A statement already restricted to the current tenant.

        This is the single place tenancy is applied on the read path. Every
        public method routes through it.
        """
        return select(self.model).where(
            self.model.tenant_id == self.tenant_id  # type: ignore[attr-defined]
        )

    # -- reads ------------------------------------------------------------
    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        """Fetch by id within the tenant, or ``None``.

        A row belonging to another tenant is indistinguishable from a row that
        does not exist. That is intentional: reporting "forbidden" would confirm
        the existence of another tenant's resource.
        """
        result = await self._session.execute(self._select().where(self.model.id == entity_id))  # type: ignore[attr-defined]
        return result.scalar_one_or_none()

    async def get_or_raise(self, entity_id: uuid.UUID) -> ModelT:
        entity = await self.get(entity_id)
        if entity is None:
            raise NotFoundError(f"{self.model.__name__} not found.")
        return entity

    async def list_all(self, *, limit: int = 100, offset: int = 0) -> Sequence[ModelT]:
        result = await self._session.execute(self._select().limit(limit).offset(offset))
        return result.scalars().all()

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(self.model)
            .where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]
        )
        return int(result.scalar_one())

    async def exists(self, **filters: Any) -> bool:
        stmt = self._select()
        for column, value in filters.items():
            stmt = stmt.where(getattr(self.model, column) == value)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    # -- writes -----------------------------------------------------------
    def add(self, entity: ModelT) -> ModelT:
        """Stage an insert.

        The tenant is assigned here rather than trusted from the caller. An
        entity constructed without a tenant, or with the wrong one, is corrected
        to the ambient scope — which is what makes it impossible for a use case
        to write into another tenant by passing the wrong argument.
        """
        entity.tenant_id = self.tenant_id  # type: ignore[attr-defined]
        self._session.add(entity)
        return entity

    async def delete(self, entity_id: uuid.UUID) -> bool:
        """Delete by id within the tenant. Returns whether a row was removed."""
        result = await self._session.execute(
            delete(self.model)
            .where(self.model.id == entity_id)  # type: ignore[attr-defined]
            .where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]
        )
        # ``rowcount`` exists on the CursorResult produced by a DML statement but
        # not on the generic Result type, hence the cast.
        return bool(cast("CursorResult[Any]", result).rowcount)

    async def flush(self) -> None:
        await self._session.flush()
