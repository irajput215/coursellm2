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

#: Public alias for the credential patterns, so output validation
#: (:mod:`coursellm.security.output`) can name the *kind* of a match without
#: duplicating the patterns. This tuple is the single definition.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = _SECRET_PATTERNS


def _scrub_text(value: str) -> str:
    """Replace anything in ``value`` that looks like a credential."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups and "scheme" in pattern.groupindex:
            value = pattern.sub(lambda m: f"{m.group('scheme')}://[redacted]@", value)
        else:
            value = pattern.sub(_REDACTION_PLACEHOLDER, value)
    return value


#: Bound on how deep redaction walks a payload. An event is expected to be a
#: shallow, JSON-shaped dict; the cap keeps a self-referential or pathologically
#: deep structure from costing more than the log line is worth.
_MAX_REDACTION_DEPTH = 12


def _redact_value(value: Any, depth: int) -> Any:
    """Scrub one value, recursing into dicts and lists.

    A credential is exactly as dangerous nested under ``tool_results`` or inside
    a list of ``messages`` as it is at the top level, so field-name matching and
    value scrubbing both apply at every level.
    """
    if depth >= _MAX_REDACTION_DEPTH:
        return _scrub_text(value) if isinstance(value, str) else value
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, dict):
        return {
            key: (
                _REDACTION_PLACEHOLDER
                if isinstance(key, str) and key.lower() in _REDACTED_FIELDS
                else _redact_value(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(item, depth + 1) for item in value)
    return value


def redact_event(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: drop or scrub sensitive values from every event.

    The walk is recursive: a sensitive *field name* anywhere in the payload is
    replaced, and a credential-shaped value inside any nested string is
    scrubbed. The top-level mapping is updated in place (structlog's contract).
    """
    for key in list(event_dict):
        if isinstance(key, str) and key.lower() in _REDACTED_FIELDS:
            event_dict[key] = _REDACTION_PLACEHOLDER
        else:
            event_dict[key] = _redact_value(event_dict[key], depth=0)
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
