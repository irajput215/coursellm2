"""Structured logging with mandatory redaction.

Logs are JSON in every non-local environment so they can be queried, and
human-readable in local development. The important part is not the format but
the redaction stage: a :class:`RedactionProcessor` runs on every event and
removes secret-shaped values *before* they reach a handler. Logging student
document text or a provider key would be a data-leak bug, and relying on
developers to remember not to do it is not a control.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, cast

import structlog

from coursellm.core.config import Settings

# Field names whose values must never be emitted, matched case-insensitively.
_REDACTED_FIELDS = frozenset(
    {
        "password",
        "hashed_password",
        "password_hash",
        "secret",
        "secret_key",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "authorization",
        "auth_header",
        "cookie",
        "set-cookie",
        "database_url",
        "redis_url",
        "connection_string",
        "content",
        "document_text",
        "chunk_content",
        "prompt",
        "messages",
    }
)

_REDACTION_PLACEHOLDER = "[redacted]"

# Value-shaped secrets that could appear inside a free-text field.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # scheme://user:password@host
    re.compile(r"(?P<scheme>[a-z][a-z0-9+.\-]*)://[^:/\s]+:(?P<pw>[^@/\s]+)@"),
)


def _scrub_text(value: str) -> str:
    """Replace anything in ``value`` that looks like a credential."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups and "scheme" in pattern.groupindex:
            value = pattern.sub(lambda m: f"{m.group('scheme')}://[redacted]@", value)
        else:
            value = pattern.sub(_REDACTION_PLACEHOLDER, value)
    return value


def redact_event(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: drop or scrub sensitive values from every event."""
    for key in list(event_dict):
        lowered = key.lower()
        if lowered in _REDACTED_FIELDS:
            event_dict[key] = _REDACTION_PLACEHOLDER
        elif isinstance(event_dict[key], str):
            event_dict[key] = _scrub_text(event_dict[key])
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging and structlog. Idempotent."""
    level = getattr(logging, settings.log_level)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        redact_event,
    ]

    if settings.log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logging (uvicorn, sqlalchemy, alembic) through the same level
    # so that third-party noise cannot bypass the configured verbosity.
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level, force=True)
    for noisy, noisy_level in (
        ("uvicorn.access", logging.WARNING),
        ("sqlalchemy.engine", logging.WARNING if not settings.db_echo else logging.INFO),
        ("httpx", logging.WARNING),
        ("httpcore", logging.WARNING),
        ("litellm", logging.WARNING),
    ):
        logging.getLogger(noisy).setLevel(noisy_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a logger bound to ``name``.

    The name is bound as a field rather than supplied to ``get_logger`` because
    the configured ``PrintLoggerFactory`` does not carry a logger name; binding
    it explicitly keeps ``logger`` present in every emitted event regardless of
    the rendering backend.
    """
    return cast(
        structlog.stdlib.BoundLogger,
        structlog.get_logger().bind(logger=name),
    )
