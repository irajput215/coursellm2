"""The conversation state that flows through the agent graph.

This module is the executable form of ``docs/architecture/agent-architecture.md``
section 2. Three properties make it worth reading before changing anything here.

* **Fields are grouped by who writes them.** The identity group is written once
  by the API entrypoint and is then immutable; every other group names the node
  or nodes that own it. A node that returns a key outside its declared set is a
  contract violation, and :func:`validate_update` turns that into a test
  failure rather than a silent mutation.
* **Evidence channels append.** ``retrieved_documents``, ``citations``,
  ``graph_entities``, ``agent_decisions`` and ``tool_calls`` use
  :func:`operator.add`; ``degraded`` and ``errors`` use :func:`merge_unique`.
  A second retrieval pass therefore accumulates evidence instead of discarding
  the first.
* **``total=False`` is deliberate.** Nodes return partial updates. The complete
  state is constructed by exactly one factory, :func:`initial_state`, which
  asserts the required keys and is the only place allowed to build a state from
  scratch. Rehydration calls the same factory.

Nothing secret enters the state: it is checkpointed, and a checkpoint is
persisted data. No provider keys, no connection strings, no document bodies
beyond the retrieved excerpts.
"""

from __future__ import annotations

import operator
import time
import uuid
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from coursellm.llm import ChatMessage


class DegradationReason(StrEnum):
    """The machine-readable banner vocabulary.

    The list is the architecture document's, extended with the four reasons the
    document names in its failure tables (``progress_unavailable``,
    ``safety_guardrail_error``, ``external_sources_unavailable``) and the generic
    ``tool_unavailable`` used by a tool whose backing subsystem is not yet
    implemented. Keeping them as enum members rather than free strings is what
    lets the UI map a banner and the tests assert a value.
    """

    RETRIEVAL_EMPTY = "retrieval_empty"
    LEXICAL_ONLY = "lexical_only"
    SEMANTIC_ONLY = "semantic_only"
    RERANKER_UNAVAILABLE = "reranker_unavailable"
    KNOWLEDGE_GRAPH_EMPTY = "knowledge_graph_empty"
    KNOWLEDGE_GRAPH_TIMEOUT = "knowledge_graph_timeout"
    GRAPH_LOW_CONFIDENCE_ONLY = "graph_low_confidence_only"
    LLM_FALLBACK_MODEL = "llm_fallback_model"
    LLM_UNAVAILABLE = "llm_unavailable"
    CHECKPOINT_UNAVAILABLE = "checkpoint_unavailable"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_ERROR = "tool_error"
    TOOL_LIMIT_REACHED = "tool_limit_reached"
    ITERATION_LIMIT_REACHED = "iteration_limit_reached"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"  # noqa: S105 - a reason code, not a secret
    DEADLINE_EXCEEDED = "deadline_exceeded"
    RECURSION_LIMIT_REACHED = "recursion_limit_reached"
    # Named in the failure tables but absent from the enumeration in section 2.
    PROGRESS_UNAVAILABLE = "progress_unavailable"
    SAFETY_GUARDRAIL_ERROR = "safety_guardrail_error"
    EXTERNAL_SOURCES_UNAVAILABLE = "external_sources_unavailable"
    # A tool whose subsystem lands in a later PR (knowledge graph, roadmaps,
    # recommendations, assessment persistence) returns empty typed data and
    # this reason rather than inventing rows.
    TOOL_UNAVAILABLE = "tool_unavailable"


Intent = Literal["tutor", "planner", "recommender", "assessment", "progress"]

#: The five routable intents, in matrix order.
INTENTS: tuple[Intent, ...] = ("tutor", "planner", "recommender", "assessment", "progress")


def merge_unique(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer for set-like channels: append what is new, preserve first-seen order."""
    out = list(left)
    for item in right:
        if item not in out:
            out.append(item)
    return out


class RetrievedDocument(TypedDict):
    """One passage in state, carrying every score that produced its position."""

    chunk_id: str
    document_id: str
    content: str
    page: int | None
    topic: str | None
    source_type: str
    semantic_rank: int | None
    lexical_rank: int | None
    graph_rank: int | None
    rrf_score: float
    rerank_score: float | None
    citation_id: str


class GraphEntity(TypedDict):
    """One node of a prerequisite closure, with its provenance."""

    concept_id: str
    name: str
    slug: str
    relation: str
    depth: int
    direction: Literal["prerequisite_of", "requires", "related_to", "contains"]
    weight: float
    confidence: float
    verified: bool
    path: list[str]
    provenance: dict[str, Any]


class Citation(TypedDict):
    """A citation as it enters the graph state (``[S1]``, ``[S2]``, ...)."""

    citation_id: str
    chunk_id: str
    document_id: str
    page: int | None
    source_type: str


class AgentDecision(TypedDict):
    node: str
    decision: str
    rationale: str
    model: str | None
    prompt_version: str | None
    latency_ms: int


class ToolCallRecord(TypedDict):
    tool: str
    arguments: dict[str, Any]
    status: Literal["ok", "error", "timeout", "denied", "unknown_tool"]
    latency_ms: int
    error: str | None


class AgentError(TypedDict):
    node: str
    error_type: str
    message: str
    retryable: bool


class TokenUsage(TypedDict):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    calls: int


class RetrievalPlan(TypedDict):
    queries: list[str]
    course_id: str | None
    document_id: str | None
    topic: str | None
    page: int | None
    source_types: list[str]
    k_per_retriever: int
    rerank_top_k: int
    include_graph: bool
    graph_max_depth: int


class StudentProgress(TypedDict):
    course_id: str | None
    mastery: dict[str, float]
    attempts: dict[str, int]
    last_seen: dict[str, str]
    weak_concepts: list[str]
    completed_steps: list[str]


class EvaluationMetadata(TypedDict):
    retrieval_config_version: str
    prompt_versions: dict[str, str]
    models: dict[str, str]
    rerank_scores: list[float]
    graph_depth_used: int
    citation_hallucinations: int
    grounded: bool
    trace_id: str


class ConversationState(TypedDict, total=False):
    """The single object that flows through the graph."""

    # --- identity and scope: written once by the API entrypoint, immutable after ---
    tenant_id: UUID
    user_id: UUID
    conversation_id: UUID
    request_id: UUID
    roles: frozenset[str]

    # --- task framing ---
    current_course_id: UUID | None
    current_topic: str | None
    learning_goal: str | None
    intent: Intent
    conversation_summary: str | None

    # --- conversation ---
    messages: Annotated[list[AnyMessage], add_messages]

    # --- evidence (append-only within a turn) ---
    retrieval_plan: RetrievalPlan | None
    retrieved_documents: Annotated[list[RetrievedDocument], operator.add]
    citations: Annotated[list[Citation], operator.add]
    graph_entities: Annotated[list[GraphEntity], operator.add]
    grounding: dict[str, Any]

    # --- personalisation ---
    student_progress: StudentProgress

    # --- agent outputs ---
    answer_draft: dict[str, Any] | None
    recommendations: list[dict[str, Any]]
    roadmap: dict[str, Any] | None
    quiz: dict[str, Any] | None
    assessment: dict[str, Any] | None

    # --- control and observability (append-only audit channels) ---
    pending_tool_calls: list[dict[str, Any]]
    agent_decisions: Annotated[list[AgentDecision], operator.add]
    tool_calls: Annotated[list[ToolCallRecord], operator.add]
    evaluation_metadata: EvaluationMetadata
    degraded: Annotated[list[DegradationReason], merge_unique]
    errors: Annotated[list[AgentError], merge_unique]

    # --- loop counters and budgets ---
    iteration_count: int
    retrieval_pass: int
    tool_call_count: int
    token_usage: TokenUsage
    started_at_ns: int
    deadline_ns: int


#: Every declared state key. ``TypedDict`` does not expose a runtime set, so it
#: is materialised once here and used by :func:`validate_update` and the tests.
STATE_KEYS: frozenset[str] = frozenset(ConversationState.__annotations__)

#: Keys a state built by :func:`initial_state` must always contain. They are the
#: ones every node may read unconditionally, so their absence is a wiring bug and
#: not a valid partial state.
REQUIRED_STATE_KEYS: frozenset[str] = frozenset(
    {
        "tenant_id",
        "user_id",
        "conversation_id",
        "request_id",
        "roles",
        "messages",
        "retrieved_documents",
        "citations",
        "graph_entities",
        "grounding",
        "student_progress",
        "pending_tool_calls",
        "agent_decisions",
        "tool_calls",
        "evaluation_metadata",
        "degraded",
        "errors",
        "iteration_count",
        "retrieval_pass",
        "tool_call_count",
        "token_usage",
        "started_at_ns",
        "deadline_ns",
    }
)


class UndeclaredStateKeyError(RuntimeError):
    """Raised when a node returns a key :class:`ConversationState` does not declare."""

    def __init__(self, keys: frozenset[str]) -> None:
        self.keys = keys
        super().__init__(
            "A node wrote state keys that ConversationState does not declare: "
            f"{sorted(keys)}. Add the key to the schema or stop writing it; a "
            "silent mutation is not an acceptable alternative."
        )


def validate_update(update: Mapping[str, Any]) -> dict[str, Any]:
    """Assert that ``update`` writes only declared keys, and return it as a dict.

    LangGraph itself raises ``InvalidUpdateError`` for an unknown channel, but
    that only fires when the whole graph runs. Calling this from a node keeps a
    node unit-testable in isolation and turns the contract into an assertion at
    the boundary the node owns.
    """
    undeclared = frozenset(update) - STATE_KEYS
    if undeclared:
        raise UndeclaredStateKeyError(undeclared)
    return dict(update)


def empty_progress(course_id: UUID | None = None) -> StudentProgress:
    """A ``StudentProgress`` with no recorded activity."""
    return StudentProgress(
        course_id=str(course_id) if course_id is not None else None,
        mastery={},
        attempts={},
        last_seen={},
        weak_concepts=[],
        completed_steps=[],
    )


def empty_usage() -> TokenUsage:
    """Zeroed token accounting."""
    return TokenUsage(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
        calls=0,
    )


def add_usage(
    existing: TokenUsage | None,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    cost_usd: float,
) -> TokenUsage:
    """Accumulate one model call onto the turn's running total.

    Returns a new mapping rather than mutating: ``token_usage`` has no reducer,
    so each writer returns the cumulative value and the last write wins by
    construction.
    """
    base = existing or empty_usage()
    prompt = base["prompt_tokens"] + max(prompt_tokens, 0)
    completion = base["completion_tokens"] + max(completion_tokens, 0)
    total = base["total_tokens"] + max(total_tokens, 0)
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cost_usd=base["cost_usd"] + max(cost_usd, 0.0),
        calls=base["calls"] + 1,
    )


def merge_evaluation_metadata(
    state: ConversationState,
    **updates: Any,
) -> EvaluationMetadata:
    """Return the state's metadata with ``updates`` applied.

    ``evaluation_metadata`` has no reducer, so every writer must return the whole
    mapping. Centralising the merge here is what stops one node from clobbering
    another's ``rerank_scores`` or ``grounded`` flag with a partial write.
    """
    current: EvaluationMetadata = dict(  # type: ignore[assignment]
        state.get("evaluation_metadata") or {}
    )
    current.update(updates)  # type: ignore[typeddict-item]
    return current


def initial_state(
    *,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    request_id: UUID,
    roles: frozenset[str],
    deadline_ns: int,
    messages: Sequence[AnyMessage] = (),
    conversation_summary: str | None = None,
    student_progress: StudentProgress | None = None,
    current_course_id: UUID | None = None,
    learning_goal: str | None = None,
    retrieval_config_version: str = "",
) -> ConversationState:
    """Build the complete starting state for one turn.

    This is the only place allowed to construct a state from scratch. It asserts
    :data:`REQUIRED_STATE_KEYS`, so a missing channel is a programming error at
    the entrypoint rather than a ``KeyError`` deep inside a node.
    """
    state = ConversationState(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        request_id=request_id,
        roles=roles,
        current_course_id=current_course_id,
        current_topic=None,
        learning_goal=learning_goal,
        conversation_summary=conversation_summary,
        messages=list(messages),
        retrieval_plan=None,
        retrieved_documents=[],
        citations=[],
        graph_entities=[],
        grounding={"grounded": False, "evidence_count": 0, "max_rerank_score": None},
        student_progress=student_progress or empty_progress(current_course_id),
        answer_draft=None,
        recommendations=[],
        roadmap=None,
        quiz=None,
        assessment=None,
        pending_tool_calls=[],
        agent_decisions=[],
        tool_calls=[],
        evaluation_metadata=EvaluationMetadata(
            retrieval_config_version=retrieval_config_version,
            prompt_versions={},
            models={},
            rerank_scores=[],
            graph_depth_used=0,
            citation_hallucinations=0,
            grounded=False,
            trace_id=str(request_id),
        ),
        degraded=[],
        errors=[],
        iteration_count=0,
        retrieval_pass=0,
        tool_call_count=0,
        token_usage=empty_usage(),
        started_at_ns=time.monotonic_ns(),
        deadline_ns=deadline_ns,
    )
    missing = REQUIRED_STATE_KEYS - set(state)
    if missing:  # pragma: no cover - the constructor above is the invariant
        msg = f"initial_state() did not populate the required keys: {sorted(missing)}"
        raise AssertionError(msg)
    return state


def message_history(state: ConversationState, *, limit: int) -> list[ChatMessage]:
    """Project the state's messages into gateway ``ChatMessage`` objects.

    Only user and assistant text is replayed: tool messages and structured
    artifacts stay in state, where they are typed, rather than being flattened
    into prose the model would have to re-parse.
    """
    history: list[ChatMessage] = []
    for message in state.get("messages") or []:
        role = getattr(message, "type", None)
        content = getattr(message, "content", "")
        if role not in {"human", "ai"} or not isinstance(content, str) or not content:
            continue
        history.append(
            ChatMessage(role="user" if role == "human" else "assistant", content=content)
        )
    return history[-limit:]


def new_request_id() -> UUID:
    """A fresh correlation id for a turn."""
    return uuid.uuid4()


__all__ = [
    "INTENTS",
    "REQUIRED_STATE_KEYS",
    "STATE_KEYS",
    "AgentDecision",
    "AgentError",
    "Citation",
    "ConversationState",
    "DegradationReason",
    "EvaluationMetadata",
    "GraphEntity",
    "Intent",
    "RetrievalPlan",
    "RetrievedDocument",
    "StudentProgress",
    "TokenUsage",
    "ToolCallRecord",
    "UndeclaredStateKeyError",
    "add_usage",
    "empty_progress",
    "empty_usage",
    "initial_state",
    "merge_evaluation_metadata",
    "merge_unique",
    "message_history",
    "new_request_id",
    "validate_update",
]
