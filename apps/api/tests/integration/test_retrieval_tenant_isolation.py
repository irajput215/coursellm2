"""Tenant isolation for both retrievers, proven as the RLS-enforcing app role.

The owner role is a superuser locally and ignores every policy, so a test that
used it to prove isolation would prove nothing. Every search below runs through
:func:`~coursellm.db.tenancy.tenant_session` as ``coursellm_app``; the owner
engine appears only to *create* the fixtures and to assert that they exist (the
positive control that makes an empty result meaningful).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.rag.ingestion.embedders import Embedder, HashingEmbedder
from coursellm.rag.retrieval import RetrievalFilters, lexical_search, semantic_search
from tests.unit.test_bm25_scoring import bm25_score, idf

pytestmark = pytest.mark.integration


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"


async def _insert_chunk(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_index: int,
    content: str,
    token_count: int,
    terms: dict[str, int],
    embedding: Sequence[float] | None = None,
    embedding_model: str = "hashing-v1",
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO chunks (id, tenant_id, document_id, course_id, content, "
                "chunk_index, token_count, starts_mid_sentence) "
                "VALUES (:id, :tenant_id, :document_id, :course_id, :content, "
                ":chunk_index, :token_count, false)"
            ),
            {
                "id": str(chunk_id),
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "course_id": str(course_id),
                "content": content,
                "chunk_index": chunk_index,
                "token_count": token_count,
            },
        )
        for term, tf in terms.items():
            await connection.execute(
                text(
                    "INSERT INTO chunk_terms (chunk_id, tenant_id, term, tf) "
                    "VALUES (:chunk_id, :tenant_id, :term, :tf)"
                ),
                {"chunk_id": str(chunk_id), "tenant_id": str(tenant_id), "term": term, "tf": tf},
            )
        if embedding is not None:
            await connection.execute(
                text(
                    "INSERT INTO chunk_embeddings "
                    "(id, tenant_id, chunk_id, embedding_model, dim, embedding) "
                    "VALUES (:id, :tenant_id, :chunk_id, :model, :dim, "
                    "CAST(:embedding AS vector))"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": str(tenant_id),
                    "chunk_id": str(chunk_id),
                    "model": embedding_model,
                    "dim": EMBEDDING_DIM,
                    "embedding": _vector_literal(embedding),
                },
            )
    return chunk_id


async def _rebuild_stats(engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM tenant_lexical_stats WHERE tenant_id = CAST(:t AS uuid)"),
            {"t": str(tenant_id)},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_lexical_stats (tenant_id, term, doc_freq) "
                "SELECT tenant_id, term, count(DISTINCT chunk_id) FROM chunk_terms "
                "WHERE tenant_id = CAST(:t AS uuid) GROUP BY tenant_id, term"
            ),
            {"t": str(tenant_id)},
        )
        await connection.execute(
            text(
                "INSERT INTO tenant_corpus_stats "
                "(tenant_id, doc_count, total_tokens, avg_doc_len) "
                "SELECT CAST(:t AS uuid), count(*), coalesce(sum(token_count), 0), "
                "coalesce(avg(token_count), 0)::float FROM chunks "
                "WHERE tenant_id = CAST(:t AS uuid) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "doc_count = EXCLUDED.doc_count, "
                "total_tokens = EXCLUDED.total_tokens, "
                "avg_doc_len = EXCLUDED.avg_doc_len"
            ),
            {"t": str(tenant_id)},
        )


async def _embed(embedder: Embedder, text_value: str) -> list[float]:
    return (await embedder.embed_documents([text_value]))[0]


async def test_tenant_a_never_retrieves_tenant_b_chunks(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    embedder = HashingEmbedder(dim=EMBEDDING_DIM)

    body = "secretterm classified passage"
    tenant_b_chunks = [
        await _insert_chunk(
            owner_engine,
            tenant_id=seeded.tenant_b_id,
            course_id=seeded.tenant_b.course_id,
            document_id=seeded.tenant_b.document_id,
            chunk_index=index,
            content=f"{body} {index}",
            token_count=8,
            terms={"secretterm": 1},
            embedding=await _embed(embedder, body),
        )
        for index in range(3)
    ]
    await _rebuild_stats(owner_engine, seeded.tenant_b_id)

    # Positive control: the owner role sees tenant B's rows, so an empty result
    # for tenant A below is isolation and not a missing fixture.
    async with owner_engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = CAST(:t AS uuid)"),
                {"t": str(seeded.tenant_b_id)},
            )
        ).scalar_one()
    assert int(stored) == 3

    scope_a = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope_a) as session:
        semantic = await semantic_search(
            session,
            scope_a,
            pg_settings,
            query=body,
            filters=RetrievalFilters(),
            k=10,
            embedder=embedder,
        )
        lexical = await lexical_search(
            session, scope_a, pg_settings, query="secretterm", filters=RetrievalFilters(), k=10
        )

    # Tenant A has no chunks at all, so neither retriever may manufacture one.
    assert semantic.results == []
    assert lexical.results == []

    # Give tenant A a chunk of its own. Semantic search now has a neighbour, but
    # it must still never be one of tenant B's.
    own_text = "publicterm open passage"
    own_chunk = await _insert_chunk(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunk_index=0,
        content=own_text,
        token_count=8,
        terms={"publicterm": 1},
        embedding=await _embed(embedder, own_text),
    )
    await _rebuild_stats(owner_engine, seeded.tenant_a_id)

    async with tenant_session(pg_settings, scope_a) as session:
        semantic = await semantic_search(
            session,
            scope_a,
            pg_settings,
            query=body,
            filters=RetrievalFilters(),
            k=10,
            embedder=embedder,
        )
        lexical = await lexical_search(
            session, scope_a, pg_settings, query="secretterm", filters=RetrievalFilters(), k=10
        )

    assert {result.chunk_id for result in semantic.results} == {own_chunk}
    assert all(result.chunk_id not in set(tenant_b_chunks) for result in semantic.results)
    assert lexical.results == []

    # Tenant B can retrieve its own chunks; the term was never the problem.
    scope_b = TenantScope(seeded.tenant_b_id)
    async with tenant_session(pg_settings, scope_b) as session:
        tenant_b_lexical = await lexical_search(
            session, scope_b, pg_settings, query="secretterm", filters=RetrievalFilters(), k=10
        )
    assert {result.chunk_id for result in tenant_b_lexical.results} == set(tenant_b_chunks)


async def test_lexical_statistics_are_the_searching_tenants_own(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    """IDF must describe the corpus the tenant may retrieve, not the global one."""
    scope_a = TenantScope(seeded.tenant_a_id)
    scope_b = TenantScope(seeded.tenant_b_id)

    a_chunk = await _insert_chunk(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunk_index=0,
        content="sharedterm alpha",
        token_count=10,
        terms={"sharedterm": 1},
    )
    b_chunks = [
        await _insert_chunk(
            owner_engine,
            tenant_id=seeded.tenant_b_id,
            course_id=seeded.tenant_b.course_id,
            document_id=seeded.tenant_b.document_id,
            chunk_index=index,
            content="sharedterm alpha",
            token_count=10,
            terms={"sharedterm": 1},
        )
        for index in range(50)
    ]
    await _rebuild_stats(owner_engine, seeded.tenant_a_id)
    await _rebuild_stats(owner_engine, seeded.tenant_b_id)

    async with tenant_session(pg_settings, scope_a) as session:
        from_a = await lexical_search(
            session, scope_a, pg_settings, query="sharedterm", filters=RetrievalFilters(), k=10
        )
    async with tenant_session(pg_settings, scope_b) as session:
        from_b = await lexical_search(
            session, scope_b, pg_settings, query="sharedterm", filters=RetrievalFilters(), k=50
        )

    assert [result.chunk_id for result in from_a.results] == [a_chunk]
    assert len(from_b.results) == 50
    assert {result.chunk_id for result in from_b.results} == set(b_chunks)

    # Tenant A: N=1, df=1 -> IDF = ln(1 + 0.5/1.5) = 0.28768.
    expected_a = bm25_score(
        query_terms=["sharedterm"],
        term_frequencies={"sharedterm": 1},
        token_count=10,
        doc_frequencies={"sharedterm": 1},
        doc_count=1,
        avg_doc_len=10.0,
    )
    # Tenant B: N=50, df=50 -> IDF = ln(1 + 0.5/50.5) = 0.00985.
    expected_b = bm25_score(
        query_terms=["sharedterm"],
        term_frequencies={"sharedterm": 1},
        token_count=10,
        doc_frequencies={"sharedterm": 50},
        doc_count=50,
        avg_doc_len=10.0,
    )

    assert from_a.results[0].score == pytest.approx(round(expected_a, 6), abs=1e-9)
    assert from_a.results[0].score == pytest.approx(0.287682, abs=1e-6)
    assert from_a.results[0].score > 0.28
    assert from_b.results[0].score == pytest.approx(round(expected_b, 6), abs=1e-9)
    assert from_b.results[0].score < 0.01

    # The stored statistics really are per-tenant, and the numbers above follow
    # from them rather than from a lucky coincidence in the query.
    assert idf(1, 1) == pytest.approx(expected_a, abs=1e-12)
    assert idf(50, 50) == pytest.approx(expected_b, abs=1e-12)
