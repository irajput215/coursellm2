"""``knowledge_graph`` — prerequisite closure and related concepts.

The closure is what vector similarity structurally cannot provide: transitive
prerequisite reasoning with depth, confidence and provenance. The real
traversal is PR 10; this node ships the plumbing, the timeout/degradation
behaviour and the grounding verdict.

A timeout or an empty closure never affects the retrieved evidence: the vector
and lexical passes already ran, and a factual question is answered from them. The
degradation is recorded so a prerequisite question is visibly marked
low-confidence rather than silently answered from documents alone.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from coursellm.agents.nodes import NodeFn
from coursellm.agents.routing import assess_grounding
from coursellm.agents.state import (
    ConversationState,
    DegradationReason,
    GraphEntity,
    RetrievalPlan,
    merge_evaluation_metadata,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.tools.graph_tools import (
    KnowledgeGraphRepository,
    KnowledgeGraphResult,
    NullKnowledgeGraphRepository,
    SearchKnowledgeGraphArgs,
)

logger = get_logger(__name__)

KNOWLEDGE_GRAPH_NODE = "knowledge_graph"

GraphSearch = Callable[[ConversationState, RetrievalPlan], Awaitable[KnowledgeGraphResult]]


def make_knowledge_graph_node(
    *,
    settings: Settings,
    repository: KnowledgeGraphRepository | None = None,
    graph_search: GraphSearch | None = None,
) -> NodeFn:
    """Build the node, bound to a repository or an explicit search function."""
    active: GraphSearch = graph_search or _default_search(
        repository or NullKnowledgeGraphRepository(), settings
    )

    async def knowledge_graph(state: ConversationState) -> dict[str, Any]:
        plan = state.get("retrieval_plan")
        include_graph = bool(plan and plan["include_graph"])
        entities: list[GraphEntity] = []
        degraded: list[DegradationReason] = []
        depth_used = 0
        calls = 0
        records: list[dict[str, Any]] = []

        if include_graph and plan is not None:
            calls = 1
            try:
                result = await active(state, plan)
            except TimeoutError:
                degraded.append(DegradationReason.KNOWLEDGE_GRAPH_TIMEOUT)
                logger.warning("knowledge_graph_timeout")
                records.append(_record("timeout", "TimeoutError: graph traversal timed out."))
            except Exception as exc:
                degraded.append(DegradationReason.KNOWLEDGE_GRAPH_EMPTY)
                logger.warning("knowledge_graph_degraded", error_type=type(exc).__name__)
                records.append(_record("error", f"{type(exc).__name__}: {exc}"))
            else:
                entities = [cast(GraphEntity, entity) for entity in result.entities]
                depth_used = result.depth_used
                degraded.extend(_reasons(result.degraded))
                records.append(_record("ok", None))
        else:
            degraded.extend([])

        grounding = assess_grounding(state, settings)
        metadata = merge_evaluation_metadata(
            state,
            grounded=bool(grounding["grounded"]),
            graph_depth_used=max(depth_used, 0),
        )
        return validate_update(
            {
                "graph_entities": entities,
                "evaluation_metadata": metadata,
                "tool_call_count": state.get("tool_call_count", 0) + calls,
                "tool_calls": records,
                "degraded": _unique(degraded),
            }
        )

    return knowledge_graph


def _default_search(repository: KnowledgeGraphRepository, settings: Settings) -> GraphSearch:
    async def search(state: ConversationState, plan: RetrievalPlan) -> KnowledgeGraphResult:
        args = SearchKnowledgeGraphArgs(
            concept_name=state.get("current_topic"),
            max_depth=plan["graph_max_depth"],
            min_confidence=settings.graph_min_traversable_confidence,
        )
        return await repository.search(tenant_id=state["tenant_id"], args=args)

    return search


def _reasons(reasons: list[str]) -> list[DegradationReason]:
    mapped: list[DegradationReason] = []
    for reason in reasons:
        if "timeout" in reason:
            mapped.append(DegradationReason.KNOWLEDGE_GRAPH_TIMEOUT)
        elif reason == "knowledge_graph_empty":
            mapped.append(DegradationReason.KNOWLEDGE_GRAPH_EMPTY)
        else:
            mapped.append(DegradationReason.KNOWLEDGE_GRAPH_EMPTY)
    return mapped


def _record(status: str, error: str | None) -> dict[str, Any]:
    return {
        "tool": "search_knowledge_graph",
        "arguments": {},
        "status": status,
        "latency_ms": 0,
        "error": error,
    }


def _unique(reasons: list[DegradationReason]) -> list[DegradationReason]:
    seen: set[DegradationReason] = set()
    result: list[DegradationReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result


__all__ = ["KNOWLEDGE_GRAPH_NODE", "GraphSearch", "make_knowledge_graph_node"]
