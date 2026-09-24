"""``retrieval`` — the tenant-scoped hybrid scan.

The node executes the :class:`~coursellm.agents.state.RetrievalPlan` the previous
node produced, by running ``hybrid_search`` for each planned query and converting
the ranked passages into typed state. It reranks nothing: the ``rerank`` node
does that, so the two halves get independent spans, budgets and fallbacks while
sharing one implementation with the ``search_documents`` tool.

Degradation is symmetric and explicit: one retriever failing yields
``lexical_only`` or ``semantic_only``; both failing yields an empty list with
``retrieval_empty``. An empty result is never an exception.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.agents.nodes import NodeFn
from coursellm.agents.state import (
    Citation,
    ConversationState,
    DegradationReason,
    RetrievalPlan,
    RetrievedDocument,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.db.models.content import SourceType
from coursellm.db.tenancy import TenantScope
from coursellm.rag.rerank.pipeline import rank
from coursellm.rag.rerank.rerankers import Reranker
from coursellm.rag.retrieval.hybrid import hybrid_search
from coursellm.rag.retrieval.types import RetrievalFilters
from coursellm.tools.documents import ranked_to_document

logger = get_logger(__name__)

RETRIEVAL_NODE = "retrieval"

#: How many of the planned queries are actually executed. A plan with more is
#: narrowed rather than refused: the first queries are the closest to the
#: student's words.
_MAX_QUERIES = 2


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Typed passages plus the reasons the scan returned fewer than it could."""

    documents: list[RetrievedDocument] = field(default_factory=list)
    degraded: list[DegradationReason] = field(default_factory=list)


RetrieveFn = Callable[[ConversationState, RetrievalPlan], Awaitable[RetrievalResult]]


def make_retrieval_node(
    *,
    settings: Settings,
    session: AsyncSession | None = None,
    retrieve: RetrieveFn | None = None,
    reranker: Reranker | None = None,
) -> NodeFn:
    """Build the node, optionally bound to a tenant-scoped session or a fake scan."""
    active: RetrieveFn = retrieve or _default_retrieve(settings, session=session, reranker=reranker)

    async def retrieval(state: ConversationState) -> dict[str, Any]:
        started = time.perf_counter()
        plan = state.get("retrieval_plan")
        if plan is None:
            return validate_update(
                {
                    "retrieved_documents": [],
                    "citations": [],
                    "retrieval_pass": state.get("retrieval_pass", 0) + 1,
                    "tool_call_count": state.get("tool_call_count", 0),
                    "tool_calls": [],
                    "degraded": [DegradationReason.RETRIEVAL_EMPTY],
                }
            )
        try:
            result = await active(state, plan)
        except Exception as exc:
            # A retriever that raises is one retriever failing; the turn still
            # proceeds and refuses from typed state rather than erroring.
            logger.warning("retrieval_node_degraded", error_type=type(exc).__name__)
            result = RetrievalResult(documents=[], degraded=[DegradationReason.RETRIEVAL_EMPTY])
        existing = state.get("citations") or []
        citations = [
            Citation(
                citation_id=document["citation_id"],
                chunk_id=document["chunk_id"],
                document_id=document["document_id"],
                page=document["page"],
                source_type=document["source_type"],
            )
            for document in result.documents
            if all(citation["citation_id"] != document["citation_id"] for citation in existing)
        ]
        latency_ms = max(int((time.perf_counter() - started) * 1000), 0)
        calls = min(max(len(plan["queries"]), 1), _MAX_QUERIES)
        record: dict[str, Any] = {
            "tool": _tool_name(plan),
            "arguments": {"queries": plan["queries"], "k": plan["k_per_retriever"]},
            "status": "ok",
            "latency_ms": latency_ms,
            "error": None,
        }
        return validate_update(
            {
                "retrieved_documents": result.documents,
                "citations": citations,
                "retrieval_pass": state.get("retrieval_pass", 0) + 1,
                "tool_call_count": state.get("tool_call_count", 0) + calls,
                "tool_calls": [record],
                "degraded": result.degraded,
            }
        )

    return retrieval


def _tool_name(plan: RetrievalPlan) -> str:
    if plan["source_types"] == ["book"]:
        return "search_books"
    return "search_documents"


def _default_retrieve(
    settings: Settings,
    *,
    session: AsyncSession | None,
    reranker: Reranker | None,
) -> RetrieveFn:
    async def retrieve(state: ConversationState, plan: RetrievalPlan) -> RetrievalResult:
        if session is None:
            return RetrievalResult(documents=[], degraded=[DegradationReason.RETRIEVAL_EMPTY])
        filters = RetrievalFilters(
            course_id=_uuid(plan["course_id"]),
            document_id=_uuid(plan["document_id"]),
            topic=plan["topic"],
            page=plan["page"],
            source_types=(
                [SourceType(value) for value in plan["source_types"]]
                if plan["source_types"]
                else None
            ),
        )
        scope = TenantScope(state["tenant_id"])
        documents: list[RetrievedDocument] = []
        degraded: list[DegradationReason] = []
        seen: set[str] = set()
        queries = plan["queries"][:_MAX_QUERIES] or [""]
        for query in queries:
            semantic, lexical = await hybrid_search(
                session,
                scope,
                settings,
                query=query,
                filters=filters,
                k_per_retriever=plan["k_per_retriever"],
            )
            ranking = await rank(
                query=query,
                semantic=semantic,
                lexical=lexical,
                settings=settings,
                reranker=reranker,
            )
            for passage in ranking.results:
                document = RetrievedDocument(**ranked_to_document(passage))  # type: ignore[typeddict-item]
                if document["chunk_id"] in seen:
                    continue
                seen.add(document["chunk_id"])
                documents.append(document)
            degraded.extend(_degradation(semantic.degraded, lexical.degraded, ranking.degraded))
        return RetrievalResult(documents=documents, degraded=_unique(degraded))

    return retrieve


def _degradation(
    semantic_degraded: list[str],
    lexical_degraded: list[str],
    ranking_degraded: list[str],
) -> list[DegradationReason]:
    reasons: list[DegradationReason] = []
    if semantic_degraded and not lexical_degraded:
        reasons.append(DegradationReason.LEXICAL_ONLY)
    if lexical_degraded and not semantic_degraded:
        reasons.append(DegradationReason.SEMANTIC_ONLY)
    if semantic_degraded and lexical_degraded:
        reasons.append(DegradationReason.RETRIEVAL_EMPTY)
    if any("rerank" in reason for reason in ranking_degraded):
        reasons.append(DegradationReason.RERANKER_UNAVAILABLE)
    return reasons


def _uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _unique(reasons: list[DegradationReason]) -> list[DegradationReason]:
    seen: set[DegradationReason] = set()
    result: list[DegradationReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result


__all__ = [
    "RETRIEVAL_NODE",
    "RetrievalResult",
    "RetrieveFn",
    "make_retrieval_node",
]
