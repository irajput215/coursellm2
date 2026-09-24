"""Application factory.

Using a factory rather than a module-level ``app = FastAPI()`` keeps import side
effects out of the picture: tests can build an app with different settings, and
nothing connects to the database merely because a module was imported. The
previous implementation ran ``create_all()`` at import time, which is exactly
the class of bug this avoids.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from coursellm import __version__
from coursellm.api.routers import health
from coursellm.core.config import Environment, Settings, get_settings
from coursellm.core.errors import register_exception_handlers
from coursellm.core.logging import configure_logging, get_logger
from coursellm.middleware import (
    REQUEST_ID_HEADER,
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
)

logger = get_logger(__name__)

_DESCRIPTION = """
**CourseLLM** is an agentic RAG tutoring platform.

It answers questions grounded in a student's own course material using hybrid
retrieval (pgvector HNSW + BM25), Reciprocal Rank Fusion and cross-encoder
reranking; reasons over a prerequisite knowledge graph; and produces
personalised roadmaps, recommendations, quizzes and progress tracking.

*Answers are grounded in retrieved evidence and carry citations. When the
evidence does not support an answer, the system says so rather than answering
from memory.*
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    Startup logs a redacted configuration summary so that every deployment is
    self-documenting in its own logs, and warns loudly about configurations that
    are valid but commonly wrong outside production.
    """
    settings = get_settings()
    logger.info(
        "application_starting",
        service=settings.app_name,
        version=__version__,
        environment=settings.environment.value,
        llm_enabled=settings.llm_enabled,
        embedding_provider=settings.embedding_provider.value,
        rerank_enabled=settings.rerank_enabled,
        otel_enabled=settings.otel_enabled,
        langsmith_enabled=settings.langsmith_enabled,
        retrieval_config_version=settings.retrieval_config_version,
    )

    if settings.environment is Environment.PROD and not settings.llm_enabled:
        logger.warning(
            "llm_disabled_in_production",
            impact="Generation will degrade to extractive answers from retrieved context.",
        )

    _register_dependency_checks(settings)
    await _verify_tenant_isolation(settings)

    try:
        yield
    finally:
        logger.info("application_stopping", service=settings.app_name)
        await _shutdown()


def _register_dependency_checks(settings: Settings) -> None:
    """Attach readiness probes for infrastructure the API cannot serve without.

    Imported lazily and guarded so the application can still start — and report
    itself unready — when an optional subsystem is unavailable, rather than
    failing at import with a traceback naming an internal module.
    """
    try:
        from coursellm.db.session import check_connection

        health.register_check(
            "database",
            lambda: check_connection(settings),
            critical=True,
            detail="PostgreSQL reachable and queryable",
        )
    except ImportError:  # pragma: no cover - database layer absent
        logger.warning("database_check_not_registered")


async def _verify_tenant_isolation(settings: Settings) -> None:
    """Confirm Row-Level Security is actually enforced for this role.

    A startup check rather than a documentation note, because the failure it
    detects is invisible. A superuser — which is what a local PostgreSQL install
    usually provides — ignores every RLS policy without warning, so the system
    looks isolated while returning other tenants' rows.

    * In production, a role that can bypass RLS is a startup failure.
    * Elsewhere it is a loud warning, so the hazard is visible during development
      instead of discovered in production.
    """
    try:
        from sqlalchemy.ext.asyncio import AsyncSession

        from coursellm.db.session import get_engine, rls_enforcement_status

        engine = await get_engine(settings)
        async with engine.connect() as conn:
            status = await rls_enforcement_status(AsyncSession(bind=conn))
    except Exception as exc:
        logger.warning(
            "tenant_isolation_check_skipped",
            reason=f"{type(exc).__name__}: {exc}",
            impact="Readiness will report the database as unavailable.",
        )
        return

    if status["enforced"]:
        logger.info("tenant_isolation_enforced", role=status["role"])
        return

    if settings.is_production:
        msg = (
            f"The database role {status['role']!r} bypasses Row-Level Security "
            f"(superuser={status['is_superuser']}, bypassrls={status['bypasses_rls']}). "
            "Refusing to start in prod: tenant isolation would rest on application "
            "code alone. Connect as a role with NOSUPERUSER NOBYPASSRLS."
        )
        raise RuntimeError(msg)

    logger.warning(
        "tenant_isolation_not_enforced",
        role=status["role"],
        is_superuser=status["is_superuser"],
        bypasses_rls=status["bypasses_rls"],
        impact=(
            "Row-Level Security is ignored for this role, so isolation rests on "
            "repository scoping alone. Run `make db-bootstrap` and connect as the "
            "coursellm_app role to exercise the database backstop locally."
        ),
    )


async def _shutdown() -> None:
    """Release process-wide resources.

    Each subsystem registers its own cleanup as it is introduced; the calls are
    guarded so that a partially initialised application still shuts down cleanly.
    """
    try:
        from coursellm.db.session import dispose_engine

        await dispose_engine()
    except ImportError:  # pragma: no cover - database layer not present yet
        pass

    try:
        from coursellm.cache import close_cache

        await close_cache()
    except ImportError:  # pragma: no cover - cache layer not present yet
        pass


def _build_api_router() -> APIRouter:
    """Assemble the versioned API surface.

    Domain routers are added here as they are implemented, so the public surface
    is auditable in one place rather than inferred from filesystem discovery.
    """
    from coursellm.api.routers import auth, chat, courses, roadmaps

    router = APIRouter()
    router.include_router(auth.router)
    router.include_router(courses.router)
    router.include_router(chat.router)
    router.include_router(roadmaps.router)
    router.include_router(roadmaps.progress_router)
    return router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the ASGI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        description=_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness probes."},
            {"name": "auth", "description": "Registration, login and session lifecycle."},
            {"name": "courses", "description": "Courses and the documents within them."},
            {
                "name": "chat",
                "description": (
                    "Grounded tutoring chat: retrieval, citations, refusal and "
                    "conversation history."
                ),
            },
            {
                "name": "roadmaps",
                "description": (
                    "Personalised, ordered learning roadmaps: creation from a goal, "
                    "deterministic effort estimates, progress-driven revision and step "
                    "advancement."
                ),
            },
            {
                "name": "progress",
                "description": (
                    "The caller's projected mastery, weak and stale concepts, learning "
                    "velocity and the next recommended action."
                ),
            },
        ],
    )

    # Middleware is applied in reverse order of registration: the last one added
    # is the outermost. CORS is outermost so that error responses produced by
    # the layers beneath it still carry CORS headers and are readable by a
    # browser. RequestContext is next so a request id exists for every log line
    # and every response, including rejections.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        # Credentials are deliberately disabled. Authentication uses a Bearer
        # token in the Authorization header, which does not require credentialed
        # CORS. Enabling it alongside a permissive origin list is the unsafe
        # combination the prototype shipped; it is not needed here.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(_build_api_router(), prefix=settings.api_v1_prefix)

    return app


__all__ = ["create_app"]
