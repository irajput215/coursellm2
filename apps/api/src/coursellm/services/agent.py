"""The agent use case: run one turn through the graph and persist the result.

The graph is the behaviour; this module is the seam between it and the product.

* **It builds the initial state from the verified tenant context**, never from a
  request field. ``tenant_id`` and ``user_id`` enter state here and are the only
  source of scope every downstream node and tool reads.
* **It binds the ambient LLM scope** so every model call inside the graph — and
  inside a tool handler — writes an ``llm_usage`` row joined to the turn by
  ``request_id``.
* **It persists the turn relationally.** The checkpointer resumes an in-flight
  turn; ``conversations`` and ``messages`` are the durable, queryable record
  (``agent-architecture.md`` section 10). A checkpoint failure never fails the
  turn.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.agents.graph import build_graph
from coursellm.agents.nodes.student_context import ProgressProvider
from coursellm.agents.state import (
    AgentDecision,
    Citation,
    ConversationState,
    DegradationReason,
    TokenUsage,
    ToolCallRecord,
    initial_state,
)
from coursellm.core.config import Settings
from coursellm.core.errors import NotFoundError
from coursellm.core.logging import get_logger
from coursellm.db.models.conversation import Conversation, Message, MessageRole
from coursellm.db.tenancy import TenantContext
from coursellm.llm import LLMGateway, ModelTask, resolve_model
from coursellm.llm.cost import count_tokens
from coursellm.llm.types import LLMScope, bind_llm_scope
from coursellm.observability import metrics, tracing
from coursellm.observability.attributes import (
    GRAPH_DEGRADED,
    GRAPH_INTENT,
    GRAPH_NAME,
    GRAPH_OUTCOME,
    GRAPH_STEPS,
    GRAPH_TOOL_CALLS,
    SPAN_AGENT_GRAPH,
)
from coursellm.services.chat import commit_turn, unique, validate_question
from coursellm.services.chat import user_message as make_user_message

logger = get_logger(__name__)

_TITLE_CHARS = 120


@dataclass(frozen=True, slots=True)
class AgentTurnResult:
    """The typed outcome of one agent turn."""

    answer: str
    citations: list[Citation]
    grounded: bool
    degraded: list[str]
    intent: str
    conversation_id: uuid.UUID
    token_usage: TokenUsage
    roadmap: dict[str, Any] | None = None
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    quiz: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    decisions: list[AgentDecision] = field(default_factory=list)
    state: ConversationState | None = None


async def run_turn(
    session: AsyncSession,
    settings: Settings,
    gateway: LLMGateway,
    context: TenantContext,
    *,
    question: str,
    course_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    checkpointer: Any = None,
    graph: CompiledStateGraph[Any, Any, Any, Any] | None = None,
    progress_provider: ProgressProvider | None = None,
) -> AgentTurnResult:
    """Run one bounded agent turn and persist it."""
    started_ns = time.monotonic_ns()
    validate_question(settings, question)
    conversation = await _resolve_conversation(
        session,
        context,
        question=question,
        course_id=course_id,
        conversation_id=conversation_id,
    )
    request_id = uuid.uuid4()
    model = resolve_model(settings, ModelTask.TUTORING)
    # Both messages are inserted in ONE transaction, and PostgreSQL's ``now()``
    # returns the *transaction* timestamp rather than the statement's. The server
    # default would therefore give the user and assistant rows identical
    # ``created_at`` values, and ``GET /chat/conversations/{id}`` orders by
    # ``(created_at, id)`` — where ``id`` is a random UUID. The transcript could
    # render the answer before the question. Pinning distinct timestamps makes the
    # order deterministic without a schema change.
    turn_started_at = datetime.now(UTC)

    user_message = make_user_message(
        context,
        settings,
        conversation_id=conversation.id,
        question=question,
        created_at=turn_started_at,
    )
    session.add(user_message)
    await session.flush()
    # Commit the question before the graph runs. A hard graph failure then rolls
    # back only the answer: the question is history, which is the transcript
    # guarantee ``services/chat`` documents and the integration suite asserts.
    await commit_turn(session, context)

    initial = initial_state(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        conversation_id=conversation.id,
        request_id=request_id,
        roles=frozenset({context.role.value}),
        deadline_ns=time.monotonic_ns() + settings.agent_turn_deadline_ms * 1_000_000,
        messages=[HumanMessage(content=question)],
        current_course_id=course_id or conversation.course_id,
        retrieval_config_version=settings.retrieval_config_version,
    )
    active_graph = graph or build_graph(
        settings=settings,
        gateway=gateway,
        session=session,
        checkpointer=checkpointer,
        progress_provider=progress_provider,
    )
    # The tenant is part of the thread id, not a checkpoint namespace. LangGraph
    # 1.2 keys top-level checkpoints by ``thread_id`` alone and treats
    # ``configurable.checkpoint_ns`` as an internal subgraph namespace, so the
    # architecture document's ``checkpoint_ns = tenant_id`` would silently do
    # nothing here. Prefixing the thread id is version-independent and gives the
    # same guarantee: two tenants that share a conversation id cannot read each
    # other's checkpoints.
    config: RunnableConfig = {
        "configurable": {
            "thread_id": checkpoint_thread_id(context.tenant_id, conversation.id),
        },
        "recursion_limit": settings.graph_recursion_limit,
    }
    final = await _invoke(active_graph, initial, config)

    answer = _answer_text(final)
    citations = _dedupe_citations(final.get("citations") or [])
    grounding = final.get("grounding") or {}
    degraded = unique([reason.value for reason in final.get("degraded") or []])
    token_usage = final.get("token_usage") or TokenUsage(
        prompt_tokens=0, completion_tokens=0, total_tokens=0, cost_usd=0.0, calls=0
    )
    intent = str(final.get("intent", "tutor"))
    raw_metadata = final.get("evaluation_metadata")
    evaluation_metadata: dict[str, Any] = (
        dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    )
    recorded_models = evaluation_metadata.get("models") or {}
    recorded_prompts = evaluation_metadata.get("prompt_versions") or {}
    assistant_message = Message(
        # Strictly after the question, so the pair always sorts correctly.
        created_at=turn_started_at + timedelta(microseconds=1),
        tenant_id=context.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=answer,
        citations=[dict(citation) for citation in citations],
        degraded=degraded,
        grounded=bool(grounding.get("grounded", False)),
        token_count=(
            token_usage["completion_tokens"]
            if token_usage["calls"]
            else count_tokens(answer, model)
        ),
        retrieval_config_version=settings.retrieval_config_version,
        # Observability columns, taken from ``evaluation_metadata`` and the
        # active span so a stored answer is attributable to the model, prompt
        # version and trace that produced it.
        intent=intent,
        model=str(recorded_models.get(intent) or model),
        prompt_version=recorded_prompts.get(intent),
        latency_ms=int((time.monotonic_ns() - started_ns) / 1_000_000),
        trace_id=tracing.current_trace_id(),
    )
    session.add(assistant_message)
    await session.flush()
    # The answer is committed here: the question's commit already ended the
    # request dependency's transaction, so the dependency would not commit this.
    await commit_turn(session, context)

    return AgentTurnResult(
        answer=answer,
        citations=citations,
        grounded=bool(grounding.get("grounded", False)),
        degraded=degraded,
        intent=intent,
        conversation_id=conversation.id,
        token_usage=token_usage,
        roadmap=final.get("roadmap"),
        recommendations=list(final.get("recommendations") or []),
        quiz=final.get("quiz"),
        assessment=final.get("assessment"),
        tool_calls=list(final.get("tool_calls") or []),
        decisions=list(final.get("agent_decisions") or []),
        state=final,
    )


def checkpoint_thread_id(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> str:
    """The tenant-scoped checkpointer key for one conversation."""
    return f"{tenant_id}:{conversation_id}"


async def _invoke(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    initial: ConversationState,
    config: RunnableConfig,
) -> ConversationState:
    """Invoke the graph, converting a recursion-limit breach into degradation.

    The whole run is one ``agent_graph`` span that covers every node span, so
    duration is inclusive and a regression is attributable to a node.
    """
    metrics.get_registry().increment("coursellm.graph.runs.active", 1.0)
    with tracing.span(SPAN_AGENT_GRAPH, **{GRAPH_NAME: "tutor_graph"}) as record:
        final: ConversationState = initial
        try:
            with bind_llm_scope(_scope_for(initial)):
                try:
                    result = await graph.ainvoke(initial, config)
                except GraphRecursionError:
                    logger.warning(
                        "agent_graph_recursion_limit", request_id=str(initial["request_id"])
                    )
                    final = await _partial(graph, config, initial)
                else:
                    final = cast(ConversationState, result)
        finally:
            metrics.get_registry().increment("coursellm.graph.runs.active", -1.0)
        _record_graph_outcome(record, final)
        return final


def _record_graph_outcome(record: tracing.SpanRecorder, state: ConversationState) -> None:
    """Attach the run's terminal attributes and record its metric."""
    intent = str(state.get("intent", "tutor"))
    degraded = [getattr(reason, "value", str(reason)) for reason in state.get("degraded") or []]
    grounded = bool((state.get("grounding") or {}).get("grounded"))
    outcome = "answered" if grounded else ("partial" if degraded else "refused")
    record.set_attributes(
        {
            GRAPH_INTENT: intent,
            GRAPH_STEPS: int(state.get("iteration_count", 0) or 0),
            GRAPH_TOOL_CALLS: len(state.get("tool_calls") or []),
            GRAPH_DEGRADED: degraded,
            GRAPH_OUTCOME: outcome,
        }
    )
    metrics.record_graph_run(
        intent=intent,
        steps=int(state.get("iteration_count", 0) or 0),
        outcome=outcome,
    )
    metrics.record_degraded(*degraded)


def _scope_for(state: ConversationState) -> LLMScope:
    return LLMScope(
        tenant_id=state["tenant_id"],
        user_id=state.get("user_id"),
        request_id=state.get("request_id"),
        conversation_id=state.get("conversation_id"),
    )


async def _partial(
    graph: CompiledStateGraph[Any, Any, Any, Any],
    config: RunnableConfig,
    initial: ConversationState,
) -> ConversationState:
    """Recover the last checkpointed values after a recursion-limit breach."""
    try:
        snapshot = await graph.aget_state(config)
        values = dict(snapshot.values) if snapshot is not None else {}
    except Exception:  # a missing checkpointer must not fail the turn
        values = {}
    if not values:
        values = dict(initial)
    values["degraded"] = [
        *(values.get("degraded") or []),
        DegradationReason.RECURSION_LIMIT_REACHED,
    ]
    return values  # type: ignore[return-value]


def _answer_text(state: ConversationState) -> str:
    draft = state.get("answer_draft")
    if isinstance(draft, dict):
        value = draft.get("text")
        if isinstance(value, str) and value.strip():
            return value
    for message in reversed(state.get("messages") or []):
        if (
            isinstance(message, AIMessage)
            and isinstance(message.content, str)
            and message.content.strip()
        ):
            return message.content
    return "I don't have enough information in your course materials to answer that question."


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    result: list[Citation] = []
    for citation in citations:
        if citation["citation_id"] in seen:
            continue
        seen.add(citation["citation_id"])
        result.append(citation)
    return result


async def _resolve_conversation(
    session: AsyncSession,
    context: TenantContext,
    *,
    question: str,
    course_id: uuid.UUID | None,
    conversation_id: uuid.UUID | None,
) -> Conversation:
    """Load the caller's conversation, or start a new one.

    A conversation id belonging to another tenant or user is reported as not
    found, never as forbidden: confirming existence would be an information leak.
    """
    if conversation_id is not None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == context.tenant_id,
            Conversation.user_id == context.user_id,
        )
        conversation = (await session.execute(statement)).scalar_one_or_none()
        if conversation is None:
            raise NotFoundError("Conversation not found.")
        return conversation
    conversation = Conversation(
        tenant_id=context.tenant_id,
        user_id=context.user_id,
        course_id=course_id,
        title=_title_from(question),
    )
    session.add(conversation)
    await session.flush()
    return conversation


def _title_from(question: str) -> str:
    collapsed = " ".join(question.split())
    if len(collapsed) <= _TITLE_CHARS:
        return collapsed or "New conversation"
    return collapsed[: _TITLE_CHARS - 1].rstrip() + "..."


__all__ = ["AgentTurnResult", "checkpoint_thread_id", "run_turn"]
