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
4. **Write confirmation.** A ``write`` tool is withheld when
   ``AGENT_REQUIRE_WRITE_CONFIRMATION`` is enabled, and a consequential write
   (one that discards recorded progress) is always withheld. The handler is not
   entered; a signed :class:`ProposedAction` is returned instead, and only
   :meth:`ToolExecutor.confirm` — which re-validates the signed proposal against
   the permission matrix and the *current* tenant — executes it.
5. **Scope injection.** ``tenant_id`` and ``user_id`` come from state.
6. **Execution.** ``asyncio.wait_for`` with the spec's timeout; one retry for
   ``none``/``read`` tools on a retryable error or timeout, zero for ``write``
   and ``external``.
7. **Audit.** A :class:`~coursellm.agents.state.ToolCallRecord` is appended for
   every attempt, including failures, and redacted arguments are recorded rather
   than the raw ones.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
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

#: A write tool may not execute until a human confirms it
#: (``docs/architecture/security.md`` section 5 and ``agent-architecture.md``
#: section 7.1). When it is withheld, the executor returns a
#: :class:`ProposedAction` instead of calling the handler, and the audit record's
#: ``error`` field carries this prefix followed by the proposal's JSON. The audit
#: channel is how the proposal reaches the API layer without adding a new state
#: channel to the graph.
PROPOSAL_ERROR_PREFIX = "proposed_action:"

#: How long a signed proposal stays confirmable.
PROPOSAL_TTL_SECONDS = 900

#: Actions that are irreversible or discard prior work are always proposed, even
#: when ``AGENT_REQUIRE_WRITE_CONFIRMATION`` is off. Additive writes execute
#: directly. Keyed by tool name; the predicate receives the validated arguments
#: and the turn's student-progress projection.
_CONSEQUENTIAL_WRITES: frozenset[str] = frozenset({"update_learning_plan"})


class Permission(StrEnum):
    """A capability a tool can require and an agent can hold.

    ``EMAIL_SEND``, ``DOCUMENTS_WRITE``, ``GRAPH_WRITE`` and ``SQL_EXECUTE`` are
    declared so that the denial is explicit and testable. No tool that requires
    them is registered, and no agent's allowlist names one.

    ``GRAPH_REVIEW`` is a first-class capability that the review queue requires
    and that *no agent holds*: inspecting unverified graph edges is a human
    action, reachable through the review UI, never through a model-chosen tool
    argument. Making it a member of this enum rather than a bare string is what
    lets the "no agent holds it" property be asserted against the same type the
    executor enforces.
    """

    DOCUMENTS_READ = "documents:read"
    COURSES_READ = "courses:read"
    GRAPH_READ = "graph:read"
    GRAPH_WRITE = "graph:write"
    GRAPH_REVIEW = "graph:review"
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
    """The audit record for a call and, when it succeeded, its typed result.

    ``proposal`` is set — and ``result`` is ``None`` — when the call was withheld
    for confirmation. The two are mutually exclusive: a proposed action has not
    executed.
    """

    record: ToolCallRecord
    result: Any = None
    proposal: ProposedAction | None = None


class ProposedAction(BaseModel):
    """A consequential write the model proposed but did not execute.

    ``docs/architecture/security.md`` section 5: the platform separates proposal
    from execution. The model never holds an execution capability for a
    consequential action; it produces this object, the API returns it to the
    client, and a deterministic endpoint re-validates and executes it only after
    an explicit confirmation.

    ``token`` is an HMAC-signed, expiring encoding of ``(agent, tool, arguments,
    tenant_id)``. The client cannot forge or tamper with a proposal: the
    signature is verified on confirmation, the tenant is re-checked against the
    authenticated request, and the arguments are re-validated against the tool's
    strict schema. ``arguments`` are deliberately *not* repeated in the public
    fields; they live inside the signed token.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    target: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    preview: str
    token: str
    expires_at: int


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
        require_write_confirmation: bool | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._session = session
        self._gateway = gateway
        #: ``None`` means "take the deployment setting"; an explicit value lets a
        #: graph builder or a test force the policy without mutating settings.
        self._require_write_confirmation = (
            settings.agent_require_write_confirmation
            if require_write_confirmation is None
            else require_write_confirmation
        )
        #: Every attempt, including failures, in execution order. The tools node
        #: copies the tail into ``state["tool_calls"]`` so the audit channel sees
        #: retries as well as final outcomes.
        self.records: list[ToolCallRecord] = []
        #: Proposals emitted but not executed, in order of proposal.
        self.proposals: list[ProposedAction] = []

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

        if self._requires_confirmation(spec, validated, state):
            proposal = self._propose(spec, validated, state, agent=agent)
            self.proposals.append(proposal)
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                # The call produced a decision, not a failure: the handler was
                # deliberately not entered. The proposal travels in the audit
                # channel so the API can return it to the client.
                status="ok",
                started=started,
                error=f"{PROPOSAL_ERROR_PREFIX}{proposal.model_dump_json()}",
            )
            return ToolOutcome(record=record, result=None, proposal=proposal)

        return await self._execute(
            spec,
            validated,
            state=state,
            tool=tool,
            recorded_arguments=recorded_arguments,
            started=started,
        )

    async def confirm(
        self,
        *,
        proposal_token: str,
        state: ConversationState,
    ) -> ToolOutcome:
        """Execute a previously proposed write after an explicit confirmation.

        The proposal is a signed token, so it cannot be forged or edited by the
        client. Confirmation re-validates every dimension the original call
        checked: the signature and expiry, the *current* tenant (from the
        authenticated request, never from the token), the agent's permission for
        the tool, the tool's side-effect class, and the arguments against the
        tool's strict schema. Only then is the handler entered.
        """
        started = time.perf_counter()
        payload = _decode_proposal(self._settings.secret_key, proposal_token)
        if payload is None:
            record = self._append(
                tool="proposed_action",
                arguments={},
                status="error",
                started=started,
                error="InvalidProposal: the token is malformed, tampered with, or expired.",
            )
            return ToolOutcome(record=record, result=None)

        tool = str(payload.get("tool", ""))
        agent = str(payload.get("agent", ""))
        recorded_arguments = _redact(payload.get("arguments") or {})

        if str(state["tenant_id"]) != str(payload.get("tenant_id")):
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="denied",
                started=started,
                error="ToolPermissionError: the proposal belongs to another tenant.",
            )
            return ToolOutcome(record=record, result=None)

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

        if spec.side_effects != "write" or not self.may_call(agent, tool):
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="denied",
                started=started,
                error=(
                    f"ToolPermissionError: agent {agent!r} may not confirm {tool!r} "
                    f"against the current permission matrix."
                ),
            )
            return ToolOutcome(record=record, result=None)

        try:
            validated = spec.parameters.model_validate(dict(payload.get("arguments") or {}))
        except PydanticValidationError as exc:
            record = self._append(
                tool=tool,
                arguments=recorded_arguments,
                status="error",
                started=started,
                error=f"ToolArgumentError: {exc.error_count()} validation error(s).",
            )
            return ToolOutcome(record=record, result=None)

        return await self._execute(
            spec,
            validated,
            state=state,
            tool=tool,
            recorded_arguments=recorded_arguments,
            started=started,
        )

    # -- internals --------------------------------------------------------
    async def _execute(
        self,
        spec: ToolSpec,
        validated: BaseModel,
        *,
        state: ConversationState,
        tool: str,
        recorded_arguments: dict[str, Any],
        started: float,
    ) -> ToolOutcome:
        """Run a validated handler under the timeout and retry policy."""
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

    def _requires_confirmation(
        self,
        spec: ToolSpec,
        validated: BaseModel,
        state: ConversationState,
    ) -> bool:
        """Whether this call must be proposed rather than executed."""
        if spec.side_effects != "write":
            return False
        if self._require_write_confirmation:
            return True
        return spec.name in _CONSEQUENTIAL_WRITES and _discards_completed_steps(
            validated, state.get("student_progress")
        )

    def _propose(
        self,
        spec: ToolSpec,
        validated: BaseModel,
        state: ConversationState,
        *,
        agent: str,
    ) -> ProposedAction:
        arguments = validated.model_dump(mode="json")
        expires_at = int(time.time()) + PROPOSAL_TTL_SECONDS
        token = _encode_proposal(
            self._settings.secret_key,
            {
                "agent": agent,
                "tool": spec.name,
                "arguments": arguments,
                "tenant_id": str(state["tenant_id"]),
                "exp": expires_at,
            },
        )
        return ProposedAction(
            action=spec.name,
            target=_proposal_target(spec.name, arguments),
            rationale=_proposal_rationale(spec.name),
            preview=_proposal_preview(spec.name, arguments),
            token=token,
            expires_at=expires_at,
        )

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


# ---------------------------------------------------------------------------
# Proposal signing
#
# The proposal token is the only client-held artefact in the confirmation flow,
# so it is authenticated rather than trusted: an HMAC over a canonical JSON body
# with the process signing key, plus an expiry. The tenant is inside the signed
# body and is re-checked against the authenticated request on confirmation, so a
# token minted for one tenant cannot be replayed for another.
# ---------------------------------------------------------------------------
def _encode_proposal(secret: str, payload: Mapping[str, Any]) -> str:
    body = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(signature)}"


def _decode_proposal(secret: str, token: str) -> dict[str, Any] | None:
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    try:
        provided = _b64url_decode(signature)
    except (ValueError, binascii.Error):
        return None
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        decoded = json.loads(_b64url_decode(body))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    try:
        expired = int(decoded.get("exp", 0)) < int(time.time())
    except (TypeError, ValueError):
        return None
    if expired:
        return None
    return decoded


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _discards_completed_steps(validated: BaseModel, progress: Mapping[str, Any] | None) -> bool:
    """Whether a plan revision drops a step the student had already completed.

    ``update_learning_plan`` is additive when it preserves completed steps and
    consequential when it does not (``security.md`` section 5). The check is
    deliberately conservative: with no recorded progress there is nothing to
    discard, and an argument shape the tool does not declare (so ``steps`` is
    absent) is not treated as consequential.
    """
    if not progress:
        return False
    completed = {str(step) for step in (progress.get("completed_steps") or [])}
    if not completed:
        return False
    steps = getattr(validated, "steps", None)
    if not isinstance(steps, list):
        return False
    preserved = {
        str(getattr(step, "concept_id", ""))
        for step in steps
        if bool(getattr(step, "completed", False))
    }
    return bool(completed - preserved)


def _proposal_target(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """The small, human-meaningful target of the proposed action."""
    keys_by_tool: dict[str, tuple[str, ...]] = {
        "update_learning_plan": ("course_id", "goal_concept_id"),
        "create_quiz": ("course_id", "n_items", "difficulty"),
        "evaluate_answer": ("quiz_attempt_id", "item_id"),
    }
    keys = keys_by_tool.get(tool, ("course_id", "document_id"))
    return {key: arguments[key] for key in keys if key in arguments}


def _proposal_rationale(tool: str) -> str:
    return {
        "update_learning_plan": (
            "This revision would discard learning-plan progress that is already recorded."
        ),
        "create_quiz": ("This action persists a new quiz draft for the student's course."),
        "evaluate_answer": (
            "This action records an assessment result and a progress event for the student."
        ),
    }.get(tool, "This write is consequential and requires explicit confirmation.")


def _proposal_preview(tool: str, arguments: Mapping[str, Any]) -> str:
    if tool == "update_learning_plan":
        steps = arguments.get("steps") or []
        goal = arguments.get("goal_concept_id", "the goal")
        return f"Replace the learning plan for {goal} with {len(steps)} step(s)."
    if tool == "create_quiz":
        return (
            f"Create a {arguments.get('difficulty', 'medium')} quiz with "
            f"{arguments.get('n_items', 0)} item(s)."
        )
    if tool == "evaluate_answer":
        return f"Record an assessment for item {arguments.get('item_id', 'unknown')}."
    return f"Execute {tool}."


__all__ = [
    "PROPOSAL_ERROR_PREFIX",
    "PROPOSAL_TTL_SECONDS",
    "Permission",
    "ProposedAction",
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
