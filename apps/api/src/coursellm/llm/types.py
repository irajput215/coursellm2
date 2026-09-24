"""Value objects shared by the model gateway.

Two rules shape this module.

**A request names a task, not a vendor model.** :class:`ModelTask` is the unit
of intent; :mod:`coursellm.llm.routing` is the only place that turns it into a
provider model id. That is what lets a model be swapped without touching domain
code (ADR-0007).

**What is persisted is deliberately impoverished.** :class:`UsageRecord` records
tokens, cost and outcome and *nothing that came from the prompt or the
completion*. Accounting is important; retaining student document text in an
observability table is not, and a schema that cannot hold it cannot leak it.

The module also hosts the ambient :class:`LLMScope`. The gateway must attribute
every attempt to a tenant, but a gateway call site should not have to thread the
tenant through its own signature, so the request layer binds it once and the
gateway reads it. The binding is a :mod:`contextvars` value rather than a global
so that concurrent requests cannot observe each other's tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict

# Bound for helpers that validate a provider response into a caller-supplied
# Pydantic schema, so ``parsed`` keeps its concrete type rather than ``Any``.
StructuredT = TypeVar("StructuredT", bound=BaseModel)


class ModelTask(StrEnum):
    """The logical capability being asked for.

    Callers choose one of these; they never choose ``gpt-4o`` or
    ``claude-3-5-haiku``. Routing owns the mapping from task to model.
    """

    CLASSIFICATION = "classification"
    EXTRACTION = "extraction"
    TUTORING = "tutoring"
    REASONING = "reasoning"


class ChatMessage(BaseModel):
    """One turn sent to a model.

    Tool messages are not used here. The gateway does not expose provider tool
    calling: tools are orchestrated by the agent layer above it, so a
    ``"tool"`` role would have no producer and would only widen the surface the
    gateway has to normalise across providers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    """A provider-agnostic request.

    ``purpose`` is a short label (for example ``"tutor.answer"``) recorded on
    the usage row. It is metadata about the call, never its text: prompt and
    completion content must not reach :mod:`coursellm.db.models.usage`.
    """

    model_config = ConfigDict(extra="forbid")

    task: ModelTask
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int | None = None
    # When set, the gateway requests JSON and validates the response against
    # this schema. Partially-valid data is never returned.
    response_model: type[BaseModel] | None = None
    purpose: str


class LLMResponse(BaseModel):
    """A normalised response, including the accounting for the call."""

    model_config = ConfigDict(extra="forbid")

    text: str
    parsed: BaseModel | None = None
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: float
    cached: bool = False
    fallback_used: bool = False
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """Exactly what gets written to ``llm_usage``.

    A plain dataclass rather than a Pydantic model: it is an internal transfer
    object constructed only by the gateway, so validation would add cost without
    protecting a boundary. ``tenant_id`` is filled from the ambient
    :class:`LLMScope` at construction time and is the only field RLS keys on.
    """

    tenant_id: uuid.UUID
    task: ModelTask
    purpose: str
    model: str
    provider: str
    used_fallback: bool
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    priced: bool
    latency_ms: int
    attempt: int
    user_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class LLMScope:
    """The tenant context an LLM call is attributed to.

    ``tenant_id`` alone is required because it is what the database policy keys
    on; the rest is optional enrichment so that background work (a worker with a
    tenant but no user) can still record usage.
    """

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    request_id: uuid.UUID | None = None
    conversation_id: uuid.UUID | None = None


_scope: ContextVar[LLMScope | None] = ContextVar("coursellm_llm_scope", default=None)


@contextmanager
def bind_llm_scope(scope: LLMScope) -> Iterator[LLMScope]:
    """Bind the ambient LLM scope for the duration of the block.

    The request layer (or a worker) calls this once per unit of work. Using a
    context variable rather than a global means two concurrent requests cannot
    attribute each other's spend.
    """
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current_llm_scope() -> LLMScope | None:
    """Return the ambient scope, or ``None`` outside a bound block."""
    return _scope.get()
