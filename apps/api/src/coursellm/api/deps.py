"""Shared FastAPI dependencies.

Dependencies are the seam where tests substitute behaviour, and where tenancy is
established. Two rules hold throughout:

* **The tenant comes from the token, never from the request.** There is no
  parameter, header or body field that can widen it. A handler cannot ask for
  another tenant's data because it has no way to express the request.
* **Session scope follows authority.** Handlers that operate on tenant data get a
  session with the tenancy variable already set, so even their first query is
  subject to Row-Level Security. Handlers that legitimately operate before a
  tenant is known — registration and login — get an unscoped session, and that
  list is short and explicit.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core import config as config_module
from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError, ValidationError
from coursellm.db.models.identity import UserRole
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantContext, tenant_session
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.repositories.identity import UserRepository
from coursellm.security.tokens import decode_access_token

# ``auto_error=False`` so a missing header produces our own error envelope with a
# request id, rather than Starlette's bare 403 whose body the frontend cannot
# parse uniformly.
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="Bearer token")


def get_settings_dep() -> Settings:
    """Resolve application settings for a request.

    Goes through the module attribute rather than a bound import so that the
    cached accessor remains overridable in tests.
    """
    return config_module.get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


async def get_unscoped_session(settings: SettingsDep) -> AsyncIterator[AsyncSession]:
    """A session with no tenant context.

    For authentication only. Under Row-Level Security this session can read
    nothing from any tenant-scoped table, which is the correct and intended
    behaviour: the login path reaches the one narrow ``SECURITY DEFINER`` function
    it needs, and nothing else.
    """
    factory = await get_session_factory(settings)
    async with factory() as session, session.begin():
        yield session


UnscopedSessionDep = Annotated[AsyncSession, Depends(get_unscoped_session)]


async def get_current_context(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TenantContext:
    """Authenticate a request from its bearer token.

    No database round trip: the token is signed and self-contained, so tenant and
    role are trustworthy once the signature verifies. Account *state* — is the
    user still active — is checked where it changes the outcome, rather than on
    every request, which keeps the hot path free of a query.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Authentication credentials were not provided.")

    claims = decode_access_token(settings, credentials.credentials)
    return TenantContext(
        tenant_id=claims.tenant_id,
        user_id=claims.subject,
        role=claims.role,
    )


ContextDep = Annotated[TenantContext, Depends(get_current_context)]


async def get_tenant_session(
    settings: SettingsDep, context: ContextDep
) -> AsyncIterator[AsyncSession]:
    """A transactional session with the tenancy variable already applied."""
    async with tenant_session(settings, context) as session:
        yield session


TenantSessionDep = Annotated[AsyncSession, Depends(get_tenant_session)]


def require_role(
    *allowed: UserRole,
) -> Callable[[TenantContext], Awaitable[TenantContext]]:
    """Build a dependency requiring one of ``allowed`` roles.

    A dependency rather than a decorator, so the requirement is visible in the
    route signature and appears in the generated OpenAPI documentation.
    """

    async def _dependency(context: ContextDep) -> TenantContext:
        context.require_role(*allowed)
        return context

    return _dependency


# ---------------------------------------------------------------------------
# Repositories
#
# Constructed per request from the scoped session and the authenticated context.
# There is no way to obtain one without a context, which is what makes "forgot
# the tenant filter" unrepresentable rather than merely discouraged.
# ---------------------------------------------------------------------------
def get_course_repository(session: TenantSessionDep, context: ContextDep) -> CourseRepository:
    return CourseRepository(session, context)


def get_document_repository(session: TenantSessionDep, context: ContextDep) -> DocumentRepository:
    return DocumentRepository(session, context)


def get_user_repository(session: TenantSessionDep, context: ContextDep) -> UserRepository:
    return UserRepository(session, context)


CourseRepositoryDep = Annotated[CourseRepository, Depends(get_course_repository)]
DocumentRepositoryDep = Annotated[DocumentRepository, Depends(get_document_repository)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]


def as_uuid(value: str) -> uuid.UUID:
    """Parse a path parameter, raising a 422 rather than a 500 on malformed input."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ValidationError("The provided identifier is not a valid UUID.") from exc


# ---------------------------------------------------------------------------
# LLM gateway
#
# The imports live at the end of the file rather than in the block at the top
# because this dependency was added by appending to an existing module. The
# gateway is process-wide (settings are process-wide), so it is resolved once
# and cached; the session factory is injected so usage rows can be written
# without the caller handling accounting.
# ---------------------------------------------------------------------------
from coursellm.llm.gateway import LLMGateway, get_gateway  # noqa: E402
from coursellm.llm.types import LLMScope, bind_llm_scope  # noqa: E402


def _request_id_uuid() -> uuid.UUID | None:
    """Best-effort conversion of the middleware request id to a UUID.

    The correlation id in ``structlog`` is a hex string or a sanitised
    client-supplied token, so it is only usable as ``llm_usage.request_id`` when
    it happens to parse; otherwise the usage row simply has no request id rather
    than a malformed one.
    """
    import structlog

    raw = structlog.contextvars.get_contextvars().get("request_id")
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        return None


async def get_llm_gateway(settings: SettingsDep, context: ContextDep) -> AsyncIterator[LLMGateway]:
    """Resolve the model gateway for an authenticated request.

    ``EchoGateway`` is returned when ``llm_enabled`` is false, so local runs and
    tests need no provider key. The ambient scope is bound here — from the token
    derived tenant, never from a request parameter — so the gateway can write
    usage rows without the handler threading tenant or user through its own
    signature.
    """
    session_factory = await get_session_factory(settings)
    gateway = get_gateway(settings, session_factory)
    scope = LLMScope(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        request_id=_request_id_uuid(),
    )
    with bind_llm_scope(scope):
        yield gateway


LLMGatewayDep = Annotated[LLMGateway, Depends(get_llm_gateway)]
