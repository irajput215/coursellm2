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
from coursellm.services.chat import unique, validate_question

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
    user_message = Message(
        tenant_id=context.tenant_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=question,
        citations=[],
        degraded=[],
        grounded=False,
        token_count=count_tokens(question, model),
        retrieval_config_version=settings.retrieval_config_version,
    )
    session.add(user_message)
    await session.flush()

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
    assistant_message = Message(
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
    )
    session.add(assistant_message)
    await session.flush()

    return AgentTurnResult(
        answer=answer,
        citations=citations,
        grounded=bool(grounding.get("grounded", False)),
        degraded=degraded,
        intent=str(final.get("intent", "tutor")),
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
    """Invoke the graph, converting a recursion-limit breach into degradation."""
    with bind_llm_scope(_scope_for(initial)):
        try:
            result = await graph.ainvoke(initial, config)
        except GraphRecursionError:
            logger.warning("agent_graph_recursion_limit", request_id=str(initial["request_id"]))
            return await _partial(graph, config, initial)
    return cast(ConversationState, result)


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
