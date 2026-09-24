"""Domain error hierarchy and FastAPI exception handlers.

The rule enforced here: **internal exception text never reaches a client.** The
previous implementation returned ``str(exception)`` in HTTP 500 bodies, which
leaks stack details, SQL fragments and provider messages. Errors are translated
to a stable, machine-readable ``ErrorResponse`` whose ``detail`` is a safe,
human-authored message, with the real cause logged and correlated by
``request_id``.

Status codes are written as integer literals rather than ``starlette.status``
constants because Starlette renamed several of them (422 and 413) across
versions; a literal is unambiguous and cannot emit a deprecation warning.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from coursellm.core.logging import get_logger

logger = get_logger(__name__)

HTTP_400_BAD_REQUEST = 400
HTTP_401_UNAUTHORIZED = 401
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_413_CONTENT_TOO_LARGE = 413
HTTP_415_UNSUPPORTED_MEDIA_TYPE = 415
HTTP_422_UNPROCESSABLE_CONTENT = 422
HTTP_429_TOO_MANY_REQUESTS = 429
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_502_BAD_GATEWAY = 502
HTTP_503_SERVICE_UNAVAILABLE = 503


class ErrorResponse(BaseModel):
    """Stable error envelope returned for every handled failure."""

    error: str = Field(description="Machine-readable error code, e.g. 'not_found'.")
    detail: str = Field(description="Safe, human-readable explanation.")
    request_id: str | None = Field(default=None, description="Correlation id for support.")
    fields: dict[str, Any] | None = Field(
        default=None, description="Per-field validation errors, when applicable."
    )


class CourseLLMError(Exception):
    """Base class for all application errors.

    Subclasses declare the HTTP status and a stable error code, so routers can
    raise a domain error without importing FastAPI.
    """

    status_code: int = HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, detail: str, *, fields: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fields = fields


class NotFoundError(CourseLLMError):
    status_code = HTTP_404_NOT_FOUND
    error_code = "not_found"


class ConflictError(CourseLLMError):
    status_code = HTTP_409_CONFLICT
    error_code = "conflict"


class ValidationError(CourseLLMError):
    status_code = HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "validation_error"


class AuthenticationError(CourseLLMError):
    status_code = HTTP_401_UNAUTHORIZED
    error_code = "not_authenticated"


class PermissionDeniedError(CourseLLMError):
    """Raised when authentication succeeded but authorisation did not.

    Deliberately indistinct from :class:`NotFoundError` at the API boundary for
    tenant-scoped resources: revealing that a resource exists in another tenant
    is itself an information leak. Callers should prefer ``NotFoundError`` when
    the existence of the resource is sensitive.
    """

    status_code = HTTP_403_FORBIDDEN
    error_code = "forbidden"


class RateLimitError(CourseLLMError):
    status_code = HTTP_429_TOO_MANY_REQUESTS
    error_code = "rate_limited"


class PayloadTooLargeError(CourseLLMError):
    status_code = HTTP_413_CONTENT_TOO_LARGE
    error_code = "payload_too_large"


class UnsupportedMediaTypeError(CourseLLMError):
    status_code = HTTP_415_UNSUPPORTED_MEDIA_TYPE
    error_code = "unsupported_media_type"


class SafetyError(CourseLLMError):
    """Raised when a request is refused by the AI safety layer."""

    status_code = HTTP_400_BAD_REQUEST
    error_code = "unsafe_request"


class UpstreamError(CourseLLMError):
    """A dependency (LLM provider, embedding service) failed irrecoverably."""

    status_code = HTTP_502_BAD_GATEWAY
    error_code = "upstream_error"


class ServiceUnavailableError(CourseLLMError):
    status_code = HTTP_503_SERVICE_UNAVAILABLE
    error_code = "service_unavailable"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so every failure returns the same envelope."""

    @app.exception_handler(CourseLLMError)
    async def _domain_error(request: Request, exc: CourseLLMError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error(
                "domain_error",
                error_code=exc.error_code,
                detail=exc.detail,
                path=request.url.path,
            )
        else:
            logger.info(
                "domain_error",
                error_code=exc.error_code,
                detail=exc.detail,
                path=request.url.path,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error=exc.error_code,
                detail=exc.detail,
                request_id=_request_id(request),
                fields=exc.fields,
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_422_UNPROCESSABLE_CONTENT,
            content=ErrorResponse(
                error="validation_error",
                detail="The request body or parameters failed validation.",
                request_id=_request_id(request),
                fields={"errors": exc.errors()},
            ).model_dump(exclude_none=True, mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error="http_error",
                detail=str(exc.detail),
                request_id=_request_id(request),
            ).model_dump(exclude_none=True),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # The exception is logged in full; the client receives only a
        # correlation id. This is the boundary that keeps internals internal.
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                error="internal_error",
                detail="An unexpected error occurred. Quote the request id when reporting it.",
                request_id=_request_id(request),
            ).model_dump(exclude_none=True),
        )
