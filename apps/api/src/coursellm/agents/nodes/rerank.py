"""``rerank`` — cross-encoder rescoring of the fused candidate list.

Separate from ``retrieval`` even though the ``search_documents`` tool composes
both: the split gives the retrieval half and the reranking half independent spans,
budgets and fallbacks from one implementation.

The node is append-safe. ``retrieved_documents`` uses an ``operator.add`` reducer,
so it cannot replace a passage in place; instead it emits scored copies of the
passages that have no score yet, and consumers collapse duplicates by
``chunk_id`` keeping the last (scored) copy. RRF order is retained when the
reranker is unavailable, slow or disabled, which is a degradation and never an
error.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from coursellm.agents.nodes import NodeFn
from coursellm.agents.routing import grounding_for
from coursellm.agents.state import (
    ConversationState,
    DegradationReason,
    RetrievedDocument,
    merge_evaluation_metadata,
    validate_update,
)
from coursellm.core.config import Settings
from coursellm.core.logging import get_logger
from coursellm.db.models.content import SourceType
from coursellm.rag.rerank.rerankers import Reranker, get_reranker
from coursellm.rag.retrieval.types import SearchResult

logger = get_logger(__name__)

RERANK_NODE = "rerank"


def make_rerank_node(
    *,
    settings: Settings,
    reranker: Reranker | None = None,
) -> NodeFn:
    """Build the node, optionally bound to a deterministic reranker double."""

    async def rerank(state: ConversationState) -> dict[str, Any]:
        documents = state.get("retrieved_documents") or []
        pending = [document for document in documents if document.get("rerank_score") is None]
        degraded: list[DegradationReason] = []
        scores: list[float] = []
        scored: list[RetrievedDocument] = []

        if pending and settings.rerank_enabled:
            active = reranker if reranker is not None else get_reranker(settings)
            results = [_to_search_result(document) for document in pending]
            query = _query(state)
            try:
                ranked = await asyncio.wait_for(
                    active.rerank(query, results, top_k=len(results)),
                    timeout=settings.rerank_timeout_ms / 1000.0,
                )
            except TimeoutError:
                degraded.append(DegradationReason.RERANKER_UNAVAILABLE)
                logger.warning("agent_rerank_timeout", timeout_ms=settings.rerank_timeout_ms)
            except Exception as exc:
                degraded.append(DegradationReason.RERANKER_UNAVAILABLE)
                logger.warning("agent_rerank_degraded", error_type=type(exc).__name__)
            else:
                by_id = {document["chunk_id"]: document for document in pending}
                for item in ranked:
                    document = by_id.get(str(item.chunk_id))
                    if document is None:
                        continue
                    scores.append(item.rerank_score)
                    scored.append(
                        RetrievedDocument(
                            chunk_id=document["chunk_id"],
                            document_id=document["document_id"],
                            content=document["content"],
                            page=document["page"],
                            topic=document["topic"],
                            source_type=document["source_type"],
                            semantic_rank=document["semantic_rank"],
                            lexical_rank=document["lexical_rank"],
                            graph_rank=document["graph_rank"],
                            rrf_score=document["rrf_score"],
                            rerank_score=item.rerank_score,
                            citation_id=document["citation_id"],
                        )
                    )

        # Grounding is computed over the merged view: every passage the retrieval
        # node produced, with a scored copy preferred where one exists. A reranker
        # timeout therefore still counts the RRF-ordered passages as evidence.
        merged_by_id: dict[str, RetrievedDocument] = {
            document["chunk_id"]: document for document in documents
        }
        for document in scored:
            merged_by_id[document["chunk_id"]] = document
        merged = list(merged_by_id.values())
        grounding = grounding_for(merged, bool(state.get("citations")), settings)
        metadata = merge_evaluation_metadata(
            state,
            grounded=bool(grounding["grounded"]),
            rerank_scores=[*scores],
        )
        return validate_update(
            {
                "retrieved_documents": scored,
                "evaluation_metadata": metadata,
                "degraded": _unique([*degraded]),
            }
        )

    return rerank


def _to_search_result(document: RetrievedDocument) -> SearchResult:
    """Reconstruct the minimal ``SearchResult`` the reranker scores.

    ``course_id`` and ``token_count`` are not evidence-graph channels, so they are
    placeholders here; the reranker reads only ``content`` and ``chunk_id``, and
    the authoritative values remain in the database.
    """
    return SearchResult(
        chunk_id=uuid.UUID(document["chunk_id"]),
        document_id=uuid.UUID(document["document_id"]),
        course_id=uuid.UUID(int=0),
        content=document["content"],
        page=document["page"],
        topic=document["topic"],
        token_count=max(len(document["content"].split()), 1),
        source_type=SourceType(document["source_type"]),
        rank=0,
        score=document["rrf_score"],
        retriever="semantic",
    )


def _query(state: ConversationState) -> str:
    plan = state.get("retrieval_plan")
    if plan and plan["queries"]:
        return plan["queries"][0]
    return ""


def _unique(reasons: list[DegradationReason]) -> list[DegradationReason]:
    seen: set[DegradationReason] = set()
    result: list[DegradationReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return result


__all__ = ["RERANK_NODE", "make_rerank_node"]
