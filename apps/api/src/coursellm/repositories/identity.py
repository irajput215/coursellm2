"""Repositories for tenants, users and authentication lookups."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.db.models.identity import Tenant, User, UserRole
from coursellm.repositories.base import TenantRepository


@dataclass(frozen=True, slots=True)
class AuthLookupResult:
    """The minimum information the login path needs.

    Deliberately not a :class:`User` ORM instance: this is produced by a
    ``SECURITY DEFINER`` function that runs outside Row-Level Security, so the
    surface is kept as small as the authentication decision requires.
    """

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: UserRole
    hashed_password: str
    is_active: bool
    tenant_is_active: bool


class TenantDirectory:
    """Access to the ``tenants`` table, which sits outside the RLS boundary.

    Because ``tenants`` has no policy (a policy would make tenant creation
    impossible), tenancy here is enforced by *how the methods are shaped* rather
    than by the database:

    * :meth:`create` is the only way to make a tenant, and it is used solely by
      registration.
    * There is no ``list_all``. Enumerating tenants is not a capability any code
      path needs, so it does not exist to be misused.
    * :meth:`get` requires an explicit id, which callers obtain from a verified
      token.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, name: str, slug: str) -> Tenant:
        tenant = Tenant(name=name, slug=slug)
        self._session.add(tenant)
        await self._session.flush()
        return tenant

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(select(Tenant).where(Tenant.slug == slug))
        return result.scalar_one_or_none()

    async def slug_exists(self, slug: str) -> bool:
        return await self.get_by_slug(slug) is not None


class UserRepository(TenantRepository[User]):
    """Tenant-scoped reads and writes over ``users``."""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Look up a user *within the current tenant*.

        Note what this is not: it is not the login path. Login must find a user
        before a tenant is known, and under RLS this query cannot do that. See
        :class:`AuthLookup`.
        """
        stmt = self._select().where(User.email == email.strip().lower())
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
        full_name: str | None = None,
        role: UserRole = UserRole.MEMBER,
    ) -> User:
        user = User(
            email=email.strip().lower(),
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            is_active=True,
        )
        return self.add(user)

    async def list_members(self, *, limit: int = 100) -> list[User]:
        stmt = self._select().order_by(User.created_at).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class AuthLookup:
    """The single, auditable exception to the tenancy boundary.

    Authentication happens *before* a tenant is known, so it cannot go through a
    tenant-scoped repository, and it must not run with RLS bypassed — that would
    be one grant away from having no isolation anywhere.

    The compromise is a ``SECURITY DEFINER`` SQL function that returns only the
    columns the login decision needs, for exactly one email. It cannot enumerate
    users, cannot read documents, and cannot be redirected by a caller because it
    pins ``search_path``.

    This class is the only caller, and it is used only by the authentication
    service. A test asserts that no other module imports it.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_for_login(self, email: str) -> AuthLookupResult | None:
        result = await self._session.execute(
            text(
                "SELECT user_id, tenant_id, role, hashed_password, is_active, tenant_is_active "
                "FROM auth_login_lookup(:email)"
            ),
            {"email": email},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        return AuthLookupResult(
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            role=UserRole(row["role"]),
            hashed_password=row["hashed_password"],
            is_active=row["is_active"],
            tenant_is_active=row["tenant_is_active"],
        )
