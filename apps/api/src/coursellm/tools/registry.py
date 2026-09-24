"""The typed tool registry and the executor that enforces its policy.

``docs/architecture/agent-architecture.md`` section 7 is the specification. The
executor is the one place in the system where a capability is granted, so it is
also the one place where permission checks, scope injection, argument
validation, timeouts, retries and audit records happen. An agent node never
calls a handler directly; it writes ``pending_tool_calls`` and the ``tools`` or
``retrieval`` node executes them through here.

Enforcement order (a denied call must not reach the handler):

1. **Registry membership.** An unregistered name is an :class:`UnknownToolError`
   and a ``status="unknown_tool"`` record. There is no schema to bind, so a model
   that emits ``send_email`` cannot even be argued with.
2. **Permission check.** The agent's grant is the union of the
   ``required_permissions`` of its allowlisted tools. A call whose requirements
   are not a subset is rejected *before* validation and before the handler, so no
   query runs and no side effect occurs.
3. **Argument validation.** ``parameters.model_validate`` with ``extra="forbid"``.
   ``tenant_id`` and ``user_id`` are not fields, so supplying one is a validation
   error rather than a silently honoured parameter.
4. **Scope injection.** ``tenant_id`` and ``user_id`` come from state.
5. **Execution.** ``asyncio.wait_for`` with the spec's timeout; one retry for
   ``none``/``read`` tools on a retryable error or timeout, zero for ``write``
   and ``external``.
6. **Audit.** A :class:`~coursellm.agents.state.ToolCallRecord` is appended for
   every attempt, including failures, and redacted arguments are recorded rather
   than the raw ones.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.agents.state import ConversationState, ToolCallRecord
from coursellm.core.config import Settings
from coursellm.llm.gateway import LLMGateway

#: Tool names are dotted-free lowercase identifiers; a malformed name is a
#: programming error, not something to normalise away.
_ALLOWED_SIDE_EFFECTS: frozenset[str] = frozenset({"none", "read", "write", "external"})

# Substrings that mark an argument as sensitive. Used only for the audit record:
# the handler still receives the real value.
_SENSITIVE_MARKERS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "authorization",
    "credential",
)

_REDACTED = "[redacted]"


class Permission(StrEnum):
    """A capability a tool can require and an agent can hold.

    ``EMAIL_SEND``, ``DOCUMENTS_WRITE``, ``GRAPH_WRITE`` and ``SQL_EXECUTE`` are
    declared so that the denial is explicit and testable. No tool that requires
    them is registered, and no agent's allowlist names one.
    """

    DOCUMENTS_READ = "documents:read"
    COURSES_READ = "courses:read"
    GRAPH_READ = "graph:read"
    GRAPH_WRITE = "graph:write"
    PROGRESS_READ = "progress:read"
    PROGRESS_WRITE = "progress:write"
    QUIZ_WRITE = "quiz:write"
    PLAN_WRITE = "plan:write"
    RESOURCES_READ = "resources:read"
    WEB_READ = "web:read"
    # Declared so the denial is explicit, granted to no agent:
    EMAIL_SEND = "email:send"
    DOCUMENTS_WRITE = "documents:write"
    SQL_EXECUTE = "sql:execute"


SideEffects = Literal["none", "read", "write", "external"]

ToolStatus = Literal["ok", "error", "timeout", "denied", "unknown_tool"]


class ToolError(Exception):
    """Base for the executor's typed failures."""

    status: ToolStatus = "error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class UnknownToolError(ToolError):
    """The tool name is not in the registry. Nothing can be bound to it."""

    status: ToolStatus = "unknown_tool"


class ToolPermissionError(ToolError):
    """The agent's grant does not cover the tool's required permissions."""

    status: ToolStatus = "denied"


class ToolArgumentError(ToolError):
    """The arguments did not validate against the tool's schema."""


class RetryableToolError(ToolError):
    """A handler failure that a read-only retry may recover from.

    A write handler must never raise this to obtain a retry: the executor refuses
    to retry writes regardless of the error class.
    """


ToolHandler = Callable[[Any, "ToolContext"], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolContext:
    """Everything a handler receives that did not come from model output.

    ``tenant_id`` and ``user_id`` are injected from state. The session is the
    caller's tenant-scoped one, so a handler inherits Row-Level Security rather
    than opening its own connection.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    settings: Settings
    session: AsyncSession | None = None
    gateway: LLMGateway | None = None
    progress: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The contract of one tool: schema, permissions, policy, timeout, handler."""

    name: str
    description: str
    parameters: type[BaseModel]
    required_permissions: frozenset[Permission]
    side_effects: SideEffects
    timeout_ms: int
    handler: ToolHandler

    @property
    def json_schema(self) -> dict[str, Any]:
        """The schema a model is shown. Generated from the args model, so it cannot drift."""
        return self.parameters.model_json_schema()


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """The audit record for a call and, when it succeeded, its typed result."""

    record: ToolCallRecord
    result: Any = None


class ToolRegistry:
    """A static, import-time registry. Nothing can register a tool at runtime."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not spec.name:
            msg = "A tool must have a non-empty name."
            raise ValueError(msg)
        if spec.name in self._specs:
            msg = f"Tool {spec.name!r} is already registered."
            raise ValueError(msg)
        if spec.side_effects not in _ALLOWED_SIDE_EFFECTS:
            msg = f"Tool {spec.name!r} declares unknown side effects {spec.side_effects!r}."
            raise ValueError(msg)
        if spec.timeout_ms <= 0:
            msg = f"Tool {spec.name!r} must declare a positive timeout."
            raise ValueError(msg)
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> ToolSpec:
        """Return the spec or raise :class:`UnknownToolError`."""
        spec = self._specs.get(name)
        if spec is None:
            msg = f"Tool {name!r} is not registered."
            raise UnknownToolError(msg)
        return spec

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def __contains__(self, name: object) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)


def tool(
    *,
    registry: ToolRegistry,
    name: str,
    description: str,
    parameters: type[BaseModel],
    required_permissions: frozenset[Permission] | set[Permission],
    side_effects: SideEffects,
    timeout_ms: int,
) -> Callable[[ToolHandler], ToolHandler]:
    """Register a handler as a tool. Duplicate names fail at import time."""

    def decorate(handler: ToolHandler) -> ToolHandler:
        registry.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                required_permissions=frozenset(required_permissions),
                side_effects=side_effects,
                timeout_ms=timeout_ms,
                handler=handler,
            )
        )
        return handler

    return decorate


class ToolExecutor:
    """Executes declared tool calls under the permission matrix and the policy."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        settings: Settings,
        session: AsyncSession | None = None,
        gateway: LLMGateway | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._session = session
        self._gateway = gateway
        #: Every attempt, including failures, in execution order. The tools node
        #: copies the tail into ``state["tool_calls"]`` so the audit channel sees
        #: retries as well as final outcomes.
        self.records: list[ToolCallRecord] = []

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def granted(self, agent: str) -> frozenset[Permission]:
        """The union of the required permissions of the agent's allowlisted tools.

        Derived from the same matrix the enforcement uses, and computed against
        the live registry so an unregistered tool (``search_web_sources`` with
        the flag off) grants nothing.
        """
        from coursellm.tools.permissions import AGENT_TOOLS

        allowed = AGENT_TOOLS.get(agent, frozenset())
        grants: set[Permission] = set()
        for name in allowed:
            spec = self._registry.get(name)
            if spec is not None:
                grants.update(spec.required_permissions)
        return frozenset(grants)

    def may_call(self, agent: str, tool: str) -> bool:
        """Whether ``agent`` is allowed to call ``tool``, by name and by permission.

        Both checks are required. The allowlist is the matrix row, and the
        permission subset is the capability. Checking only the union of
        permissions would let the assessment agent call ``search_books`` because
        it happens to hold ``documents:read`` for ``search_documents``; checking
        only the name would let an allowlisted tool require a capability the
        agent was never granted.
        """
        from coursellm.tools.permissions import AGENT_TOOLS

        if tool not in AGENT_TOOLS.get(agent, frozenset()):
            return False
        spec = self._registry.get(tool)
        if spec is None:
            return False
        return spec.required_permissions <= self.granted(agent)

    async def call(
        self,
        *,
        agent: str,
        tool: str,
        arguments: Mapping[str, Any],
        state: ConversationState,
    ) -> ToolOutcome:
        """Execute one call and return its audit record.

        Never raises for a tool-level failure: the record's ``status`` carries
        the outcome so a mid-turn failure degrades and the turn continues. The
        graph never surfaces a tool exception to the user.
        """
        started = time.perf_counter()
        recorded_arguments = _redact(arguments)

        spec = self._registry.get(tool)
        if spec is None:
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="unknown_tool",
                started=started,
                error=f"UnknownToolError: tool {tool!r} is not registered.",
            )
            return ToolOutcome(record=record, result=None)

        if not self.may_call(agent, tool):
            required = sorted(permission.value for permission in spec.required_permissions)
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="denied",
                started=started,
                error=(
                    f"ToolPermissionError: agent {agent!r} may not call {tool!r} "
                    f"(requires {required})."
                ),
            )
            return ToolOutcome(record=record, result=None)

        if spec.side_effects == "external" and not self._settings.agent_allow_external_tools:
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="denied",
                started=started,
                error=(
                    "ToolPermissionError: external tools require AGENT_ALLOW_EXTERNAL_TOOLS=true."
                ),
            )
            return ToolOutcome(record=record, result=None)

        try:
            validated = spec.parameters.model_validate(dict(arguments))
        except PydanticValidationError as exc:
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="error",
                started=started,
                error=f"ToolArgumentError: {exc.error_count()} validation error(s).",
            )
            return ToolOutcome(record=record, result=None)

        context = self._context(state)
        retries = (
            self._settings.agent_tool_max_retries if spec.side_effects in {"none", "read"} else 0
        )
        attempts = retries + 1
        outcome: ToolOutcome | None = None

        for attempt in range(1, attempts + 1):
            try:
                result = await asyncio.wait_for(
                    spec.handler(validated, context),
                    timeout=spec.timeout_ms / 1000.0,
                )
            except TimeoutError:
                record = self._append(
                    tool=tool,
                    arguments=recorded_arguments,
                    status="timeout",
                    started=started,
                    error=f"TimeoutError: exceeded {spec.timeout_ms} ms (attempt {attempt}).",
                )
                outcome = ToolOutcome(record=record, result=None)
                if attempt < attempts:
                    continue
                return outcome
            except RetryableToolError as exc:
                record = self._append(
                    tool=tool,
                    arguments=recorded_arguments,
                    status="error",
                    started=started,
                    error=f"RetryableToolError: {exc.detail}",
                )
                outcome = ToolOutcome(record=record, result=None)
                if attempt < attempts:
                    continue
                return outcome
            except Exception as exc:  # every other handler failure is terminal
                record = self._append(
                    tool=tool,
                    arguments=recorded_arguments,
                    status="error",
                    started=started,
                    error=f"{type(exc).__name__}: {exc}",
                )
                return ToolOutcome(record=record, result=None)
            else:
                record = self._append(
                    tool=tool,
                    arguments=recorded_arguments,
                    status="ok",
                    started=started,
                    error=None,
                )
                return ToolOutcome(record=record, result=result)

        return outcome or ToolOutcome(
            record=self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="error",
                started=started,
                error="ToolError: no attempt was made.",
            ),
            result=None,
        )

    # -- internals --------------------------------------------------------
    def _context(self, state: ConversationState) -> ToolContext:
        return ToolContext(
            tenant_id=state["tenant_id"],
            user_id=state.get("user_id"),
            settings=self._settings,
            session=self._session,
            gateway=self._gateway,
            progress=state.get("student_progress"),
        )

    def _append(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        status: ToolStatus,
        started: float,
        error: str | None,
    ) -> ToolCallRecord:
        record = ToolCallRecord(
            tool=tool,
            arguments=arguments,
            status=status,
            latency_ms=max(int((time.perf_counter() - started) * 1000), 0),
            error=error,
        )
        self.records.append(record)
        return record


def _redact(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Copy arguments for the audit record, masking anything secret-shaped."""
    redacted: dict[str, Any] = {}
    for key, value in arguments.items():
        if any(marker in key.lower() for marker in _SENSITIVE_MARKERS):
            redacted[key] = _REDACTED
        else:
            redacted[key] = value
    return redacted


__all__ = [
    "Permission",
    "RetryableToolError",
    "SideEffects",
    "ToolArgumentError",
    "ToolContext",
    "ToolError",
    "ToolExecutor",
    "ToolHandler",
    "ToolOutcome",
    "ToolPermissionError",
    "ToolRegistry",
    "ToolSpec",
    "ToolStatus",
    "UnknownToolError",
    "tool",
]
