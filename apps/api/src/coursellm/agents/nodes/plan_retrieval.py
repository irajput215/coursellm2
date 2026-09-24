"""``plan_retrieval`` — normalise, scope-check and clamp the model's request.

The model chooses *what to search for*; this node decides what the system is
willing to search, in what order, and how much. It is deterministic and does no
model call, so a hostile or confused retrieval request cannot widen the query.

It never fails the turn: an invalid or out-of-scope filter is dropped and logged,
``k`` is clamped to ``RETRIEVAL_TOP_K_PER_RETRIEVER``, and an empty plan yields
empty evidence rather than an error.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import HumanMessage

from coursellm.agents.nodes import NodeFn
from coursellm.agents.routing import RETRIEVAL_TOOLS
from coursellm.agents.state import (
    AgentDecision,
    ConversationState,
    RetrievalPlan,
    validate_update,
)
from coursellm.core.config import Settings

PLAN_RETRIEVAL_NODE = "plan_retrieval"

#: Signature of an injected ownership check: may ``course_id`` be searched?
ScopeCheck = Callable[[ConversationState, uuid.UUID | None], Awaitable[bool]]


def make_plan_retrieval_node(
    *,
    settings: Settings,
    scope_check: ScopeCheck | None = None,
) -> NodeFn:
    """Build the node, optionally bound to a tenant-aware course scope check."""

    async def plan_retrieval(state: ConversationState) -> dict[str, Any]:
        started = time.perf_counter()
        pending = state.get("pending_tool_calls") or []
        retrieval_calls = [call for call in pending if str(call.get("name")) in RETRIEVAL_TOOLS]
        dropped = [call for call in pending if call not in retrieval_calls]

        rationales: list[str] = []
        queries: list[str] = [
            str(call["arguments"]["query"])
            for call in retrieval_calls
            if str(call.get("name")) in {"search_documents", "search_books", "search_web_sources"}
            and isinstance(call.get("arguments"), dict)
            and call["arguments"].get("query")
        ]
        if not queries:
            queries = [_last_question(state) or (state.get("current_topic") or "")]

        requested_course = _first(
            "course_id", calls=retrieval_calls, tools={"search_documents", "search_books"}
        )
        course_id = await _resolve_course(state, requested_course, scope_check)
        if requested_course is not None and course_id is None:
            rationales.append(
                f"Dropped course_id {requested_course}: not the turn's scoped course."
            )

        source_types = _source_types(retrieval_calls)
        document_id = _first("document_id", calls=retrieval_calls, tools={"search_documents"})
        topic = _first("topic", calls=retrieval_calls, tools={"search_documents"})
        page = _first("page", calls=retrieval_calls, tools={"search_documents"})
        requested_k = _first("k", calls=retrieval_calls, tools=None)
        k = _clamp_int(
            requested_k,
            default=settings.retrieval_top_k_per_retriever,
            low=1,
            high=settings.retrieval_top_k_per_retriever,
        )
        include_graph = any(
            call.get("name") == "search_knowledge_graph" for call in retrieval_calls
        )

        plan = RetrievalPlan(
            queries=queries,
            course_id=str(course_id) if course_id else None,
            document_id=str(document_id) if document_id else None,
            topic=str(topic) if topic else None,
            page=int(page) if page else None,
            source_types=source_types,
            k_per_retriever=k,
            rerank_top_k=settings.rerank_top_k,
            include_graph=include_graph,
            graph_max_depth=settings.graph_max_depth,
        )
        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        decision = AgentDecision(
            node=PLAN_RETRIEVAL_NODE,
            decision="retrieval_planned",
            rationale=(
                f"Planned {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}, k={k}, "
                f"include_graph={include_graph}. " + " ".join(rationales)
            )[:500],
            model=None,
            prompt_version=None,
            latency_ms=latency_ms,
        )
        if dropped:
            decision["rationale"] = (
                f"{decision['rationale']} Dropped {len(dropped)} non-retrieval call(s) "
                "from this batch."
            )[:500]
        # ``pending_tool_calls`` is cleared: the retrieval calls are now a plan,
        # and a stale pending list would route the graph back into the retrieval
        # sub-path after the evidence gate.
        return validate_update(
            {
                "retrieval_plan": plan,
                "pending_tool_calls": [],
                "agent_decisions": [decision],
            }
        )

    return plan_retrieval


async def _resolve_course(
    state: ConversationState,
    requested: Any,
    scope_check: ScopeCheck | None,
) -> uuid.UUID | None:
    course_id = _as_uuid(requested)
    scoped = state.get("current_course_id")
    if course_id is None:
        return scoped
    if scope_check is not None:
        return course_id if await scope_check(state, course_id) else None
    return course_id if course_id == scoped else None


def _first(
    key: str,
    *,
    calls: list[dict[str, Any]],
    tools: set[str] | None,
) -> Any:
    for candidate in calls:
        if tools is not None and str(candidate.get("name")) not in tools:
            continue
        arguments = candidate.get("arguments")
        if isinstance(arguments, dict) and arguments.get(key) is not None:
            return arguments[key]
    return None


def _source_types(calls: list[dict[str, Any]]) -> list[str]:
    if any(call.get("name") == "search_books" for call in calls):
        return ["book"]
    for call in calls:
        arguments = call.get("arguments")
        if isinstance(arguments, dict) and arguments.get("source_types"):
            return [str(value) for value in arguments["source_types"]]
    return []


def _last_question(state: ConversationState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(parsed, high))


__all__ = ["PLAN_RETRIEVAL_NODE", "ScopeCheck", "make_plan_retrieval_node"]
