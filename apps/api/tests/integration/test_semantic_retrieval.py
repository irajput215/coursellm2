"""Semantic retrieval against pgvector, as the ``coursellm_app`` role.

The corpus is produced by the ingestion pipeline rather than by hand, so the
embeddings, chunks and the vector-space tag are exactly what production writes.
That matters for the ``embedding_model`` filter: the test must prove that a
vector from another vector space is invisible, and it can only do that if the
stored model id is the real one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.db.models.content import EMBEDDING_DIM, DocumentStatus, SourceType
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.rag.ingestion.embedders import Embedder, HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.rag.retrieval import (
    RetrievalFilters,
    RetrievalOutcome,
    hybrid_search,
    run_semantic_only,
    semantic_search,
)
from coursellm.repositories.content import DocumentRepository

pytestmark = pytest.mark.integration

_CHUNK_OVERRIDES = {
    "chunk_size_tokens": 24,
    "chunk_overlap_tokens": 6,
    "min_chunk_tokens": 1,
}


def _document_text() -> bytes:
    sentences = [
        "Transformers rely on self attention over the whole sequence.",
        "The attention mechanism mixes information across positions.",
        "Feed forward layers follow every attention sublayer.",
        "Residual connections stabilise deep transformer training.",
        "Positional encodings tell the model about token order.",
        "Scaled dot product attention is the core primitive.",
    ]
    return (" ".join(sentences * 6)).encode("utf-8")


def _settings(settings: Settings) -> Settings:
    return settings.model_copy(update=_CHUNK_OVERRIDES)


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


async def _ingest(settings: Settings, seeded, embedder: Embedder) -> int:
    scope = TenantScope(seeded.tenant_a_id)
    ingest_settings = _settings(settings)
    async with tenant_session(ingest_settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        result = await ingest_document(
            session,
            ingest_settings,
            document=document,
            data=_document_text(),
            embedder=embedder,
        )
        assert document.status is DocumentStatus.READY
    return result.chunk_count


async def _raw_distance(
    owner_engine: AsyncEngine, chunk_id: uuid.UUID, vector: Sequence[float]
) -> float:
    async with owner_engine.connect() as connection:
        value = (
            await connection.execute(
                text(
                    "SELECT embedding <=> CAST(:vector AS vector) FROM chunk_embeddings "
                    "WHERE chunk_id = :chunk_id AND embedding_model = :model"
                ),
                {
                    "vector": _vector_literal(vector),
                    "chunk_id": str(chunk_id),
                    "model": "hashing-v1",
                },
            )
        ).scalar_one()
    return float(value)


class _FailingEmbedder:
    """Fails at query time so the degradation path can be exercised."""

    model_id = "failing-v1"
    dim = EMBEDDING_DIM

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise RuntimeError("embedding backend exploded")

    async def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend exploded")


async def test_semantic_search_returns_ranked_cosine_similarities(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    chunk_count = await _ingest(pg_settings, seeded, embedder)
    assert chunk_count >= 5

    query = "attention mechanism mixes information"
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await semantic_search(
            session,
            scope,
            pg_settings,
            query=query,
            filters=RetrievalFilters(),
            k=5,
            embedder=embedder,
        )

    assert outcome.degraded == []
    assert outcome.retriever == "semantic"
    assert len(outcome.results) == 5
    assert [result.rank for result in outcome.results] == [1, 2, 3, 4, 5]
    assert all(result.retriever == "semantic" for result in outcome.results)
    assert all(-1.0 <= result.score <= 1.0 for result in outcome.results)
    scores = [result.score for result in outcome.results]
    assert scores == sorted(scores, reverse=True)
    assert all(result.token_count > 0 for result in outcome.results)
    assert all(isinstance(result.source_type, SourceType) for result in outcome.results)

    # The advertised score is a similarity, converted from the raw distance.
    query_vector = await embedder.embed_query(query)
    top = outcome.results[0]
    raw_distance = await _raw_distance(owner_engine, top.chunk_id, query_vector)
    assert top.score == pytest.approx(1.0 - raw_distance, abs=1e-9)


async def test_semantic_search_excludes_other_embedding_models(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    await _ingest(pg_settings, seeded, embedder)

    # A chunk whose embedding comes from a different vector space. Its content is
    # the query itself, so without the model filter it would be the nearest
    # neighbour and rank first.
    query = "attention mechanism mixes information"
    query_vector = await embedder.embed_query(query)
    bogus_chunk_id = uuid.uuid4()
    async with owner_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO chunks (id, tenant_id, document_id, course_id, content, "
                "chunk_index, token_count) "
                "VALUES (:id, :tenant_id, :document_id, :course_id, :content, 10000, 8)"
            ),
            {
                "id": str(bogus_chunk_id),
                "tenant_id": str(seeded.tenant_a_id),
                "document_id": str(seeded.tenant_a.document_id),
                "course_id": str(seeded.tenant_a.course_id),
                "content": query,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO chunk_embeddings "
                "(id, tenant_id, chunk_id, embedding_model, dim, embedding) "
                "VALUES (:id, :tenant_id, :chunk_id, 'bogus-model-v9', :dim, "
                "CAST(:embedding AS vector))"
            ),
            {
                "id": str(uuid.uuid4()),
                "tenant_id": str(seeded.tenant_a_id),
                "chunk_id": str(bogus_chunk_id),
                "dim": EMBEDDING_DIM,
                "embedding": _vector_literal(query_vector),
            },
        )

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await semantic_search(
            session,
            scope,
            pg_settings,
            query=query,
            filters=RetrievalFilters(),
            k=10,
            embedder=embedder,
        )

    assert outcome.degraded == []
    assert outcome.results, "the real vectors must still be retrievable"
    assert bogus_chunk_id not in {result.chunk_id for result in outcome.results}


async def test_semantic_source_type_filter_composes_with_the_ann_query(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE documents SET source_type = 'lecture' WHERE id = :id"),
            {"id": str(seeded.tenant_a.document_id)},
        )
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    await _ingest(pg_settings, seeded, embedder)

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        matching = await semantic_search(
            session,
            scope,
            pg_settings,
            query="attention mechanism",
            filters=RetrievalFilters(source_types=[SourceType.LECTURE]),
            k=5,
            embedder=embedder,
        )
        excluded = await semantic_search(
            session,
            scope,
            pg_settings,
            query="attention mechanism",
            filters=RetrievalFilters(source_types=[SourceType.BOOK]),
            k=5,
            embedder=embedder,
        )

    assert matching.degraded == []
    assert matching.results
    assert excluded.degraded == []
    assert excluded.results == []


async def test_course_filter_narrows_semantic_results(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    await _ingest(pg_settings, seeded, embedder)

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        matching = await semantic_search(
            session,
            scope,
            pg_settings,
            query="attention mechanism",
            filters=RetrievalFilters(course_id=seeded.tenant_a.course_id),
            k=5,
            embedder=embedder,
        )
        excluded = await semantic_search(
            session,
            scope,
            pg_settings,
            query="attention mechanism",
            filters=RetrievalFilters(course_id=seeded.tenant_b.course_id),
            k=5,
            embedder=embedder,
        )

    assert matching.results
    assert all(result.course_id == seeded.tenant_a.course_id for result in matching.results)
    assert excluded.results == []


async def test_semantic_search_degrades_when_the_embedder_fails(
    pg_settings: Settings, seeded
) -> None:
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await run_semantic_only(
            session,
            scope,
            pg_settings,
            query="attention",
            filters=RetrievalFilters(),
            k=5,
            embedder=_FailingEmbedder(),
        )

    assert outcome.results == []
    assert outcome.degraded == ["semantic_unavailable"]
    assert outcome.retriever == "semantic"


async def test_hybrid_search_returns_both_ranked_lists(pg_settings: Settings, seeded) -> None:
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    await _ingest(pg_settings, seeded, embedder)

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        semantic, lexical = await hybrid_search(
            session,
            scope,
            pg_settings,
            query="attention mechanism",
            filters=RetrievalFilters(),
            k_per_retriever=5,
        )

    assert isinstance(semantic, RetrievalOutcome)
    assert isinstance(lexical, RetrievalOutcome)
    assert semantic.retriever == "semantic"
    assert lexical.retriever == "lexical"
    assert semantic.degraded == []
    assert lexical.degraded == []
    assert semantic.results
    assert lexical.results
    assert all(result.retriever == "semantic" for result in semantic.results)
    assert all(result.retriever == "lexical" for result in lexical.results)
