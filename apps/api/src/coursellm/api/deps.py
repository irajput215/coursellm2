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

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.assessment.service import AssessmentService
from coursellm.core import config as config_module
from coursellm.core.config import Settings
from coursellm.core.errors import AuthenticationError, ValidationError
from coursellm.db.models.identity import UserRole
from coursellm.db.session import get_session_factory
from coursellm.db.tenancy import TenantContext, tenant_session
from coursellm.repositories.content import CourseRepository, DocumentRepository
from coursellm.repositories.identity import UserRepository
from coursellm.security.ratelimit import (
    RateLimiter,
    RateLimitExceededError,
    get_rate_limiter,
    user_key,
)
from coursellm.security.tokens import decode_access_token
from coursellm.services.roadmap import RoadmapService
from coursellm.storage.base import ObjectStore
from coursellm.storage.local import get_local_object_store

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


# ---------------------------------------------------------------------------
# Rate limiting
#
# Two ceilings share one primitive: a per-minute limit on every API route and an
# hourly limit on the LLM-backed routes (``security.md`` section 11). The key is
# derived from the *authenticated* token when one is present and falls back to
# the client address, so ``POST /auth/token`` — which has no context by
# definition and is brute-forceable — is limited too. The token decode here is
# best-effort and never raises: an invalid token is simply not an identity, and
# the route's own authentication dependency produces the 401.
# ---------------------------------------------------------------------------
async def get_rate_limiter_dep(settings: SettingsDep) -> RateLimiter:
    """Resolve the process limiter. Overridden in tests that exercise limiting."""
    return get_rate_limiter(settings)


RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter_dep)]


def _limiter_principal(
    settings: Settings,
    credentials: HTTPAuthorizationCredentials | None,
    request: Request,
) -> str:
    """A stable limiter key: the authenticated subject, or the client address."""
    if credentials is not None and credentials.credentials:
        try:
            claims = decode_access_token(settings, credentials.credentials)
        except Exception:
            claims = None
        if claims is not None:
            return f"user:{claims.subject}"
    host = getattr(getattr(request, "client", None), "host", None) or "anonymous"
    return f"ip:{host}"


async def _enforce(
    *,
    settings: Settings,
    credentials: HTTPAuthorizationCredentials | None,
    request: Request,
    limiter: RateLimiter,
    scope: str,
    limit: int,
    window_seconds: int,
    detail: str,
) -> None:
    if not settings.rate_limit_enabled:
        return
    principal = _limiter_principal(settings, credentials, request)
    decision = await limiter.check(
        user_key(principal, scope=scope), limit=limit, window_seconds=window_seconds
    )
    if not decision.allowed:
        raise RateLimitExceededError(detail, retry_after=decision.retry_after)


async def enforce_request_rate_limit(
    request: Request,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    """Per-user (or per-address) request ceiling, applied to every API route."""
    await _enforce(
        settings=settings,
        credentials=credentials,
        request=request,
        limiter=limiter,
        scope="requests",
        limit=settings.rate_limit_requests_per_minute,
        window_seconds=60,
        detail="Too many requests. Please retry after the interval in the Retry-After header.",
    )


async def enforce_llm_rate_limit(
    request: Request,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> None:
    """Per-user ceiling on LLM-backed turns, applied to the generation routes."""
    await _enforce(
        settings=settings,
        credentials=credentials,
        request=request,
        limiter=limiter,
        scope="llm",
        limit=settings.rate_limit_llm_requests_per_hour,
        window_seconds=3600,
        detail="Hourly model-request limit reached. Retry after the interval in Retry-After.",
    )


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


# ---------------------------------------------------------------------------
# Use-case services
#
# Resolved here rather than constructed inline in each router so the settings
# the service's tunable weights come from are injected once, consistently, and
# are substitutable in a test.
# ---------------------------------------------------------------------------
def get_roadmap_service(
    session: TenantSessionDep, context: ContextDep, settings: SettingsDep
) -> RoadmapService:
    return RoadmapService(session, context, settings)


RoadmapServiceDep = Annotated[RoadmapService, Depends(get_roadmap_service)]


def get_assessment_service(
    session: TenantSessionDep, context: ContextDep, settings: SettingsDep
) -> AssessmentService:
    return AssessmentService(session, context, settings)


AssessmentServiceDep = Annotated[AssessmentService, Depends(get_assessment_service)]


# ---------------------------------------------------------------------------
# Object storage
#
# The ingestion service writes uploaded bytes through the ``ObjectStore``
# protocol. Resolving it as a dependency (rather than importing a concrete store
# in the service) is what lets the deployment swap the local filesystem for S3
# with a one-line change here, and lets tests point at a temporary directory.
# ---------------------------------------------------------------------------
def get_object_store() -> ObjectStore:
    """The process object store: local filesystem in development, S3 in deployment."""
    return get_local_object_store()


ObjectStoreDep = Annotated[ObjectStore, Depends(get_object_store)]


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
