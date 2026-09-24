"""BM25 lexical retrieval against real PostgreSQL.

Corpora here are built directly with the owner engine — chunks, ``chunk_terms``
and the two statistics tables — so each test controls the document-frequency
distribution exactly. The numbers the SQL returns are compared against the pure
Python reference in :mod:`tests.unit.test_bm25_scoring`, which is the only way to
show the query implements the documented formula rather than merely being
self-consistent.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from coursellm.core.config import Settings
from coursellm.db.models.content import SourceType
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.rag.retrieval import RetrievalFilters, lexical_search
from tests.unit.test_bm25_scoring import bm25_score

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class _Chunk:
    content: str
    terms: dict[str, int]
    token_count: int
    page: int | None = None
    topic: str | None = None


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
async def _insert_chunk_rows(
    connection: AsyncConnection,
    *,
    chunk_id: uuid.UUID,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_index: int,
    content: str,
    token_count: int,
    terms: dict[str, int],
    page: int | None,
    topic: str | None,
) -> None:
    await connection.execute(
        text(
            "INSERT INTO chunks (id, tenant_id, document_id, course_id, content, page, "
            "chunk_index, token_count, topic, starts_mid_sentence) "
            "VALUES (:id, :tenant_id, :document_id, :course_id, :content, :page, "
            ":chunk_index, :token_count, :topic, false)"
        ),
        {
            "id": str(chunk_id),
            "tenant_id": str(tenant_id),
            "document_id": str(document_id),
            "course_id": str(course_id),
            "content": content,
            "page": page,
            "chunk_index": chunk_index,
            "token_count": token_count,
            "topic": topic,
        },
    )
    for term, tf in terms.items():
        await connection.execute(
            text(
                "INSERT INTO chunk_terms (chunk_id, tenant_id, term, tf) "
                "VALUES (:chunk_id, :tenant_id, :term, :tf)"
            ),
            {
                "chunk_id": str(chunk_id),
                "tenant_id": str(tenant_id),
                "term": term,
                "tf": tf,
            },
        )


async def _insert_corpus(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    chunks: Sequence[_Chunk],
    start_index: int = 0,
) -> list[uuid.UUID]:
    """Insert chunks plus their terms, then rebuild the tenant's statistics."""
    ids = [uuid.uuid4() for _ in chunks]
    async with engine.begin() as connection:
        for offset, (chunk_id, chunk) in enumerate(zip(ids, chunks, strict=True)):
            await _insert_chunk_rows(
                connection,
                chunk_id=chunk_id,
                tenant_id=tenant_id,
                course_id=course_id,
                document_id=document_id,
                chunk_index=start_index + offset,
                content=chunk.content,
                token_count=chunk.token_count,
                terms=chunk.terms,
                page=chunk.page,
                topic=chunk.topic,
            )
    await _rebuild_stats(engine, tenant_id)
    return ids


async def _insert_chunk(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk: _Chunk,
    chunk_index: int,
) -> uuid.UUID:
    chunk_id = uuid.uuid4()
    async with engine.begin() as connection:
        await _insert_chunk_rows(
            connection,
            chunk_id=chunk_id,
            tenant_id=tenant_id,
            course_id=course_id,
            document_id=document_id,
            chunk_index=chunk_index,
            content=chunk.content,
            token_count=chunk.token_count,
            terms=chunk.terms,
            page=chunk.page,
            topic=chunk.topic,
        )
    return chunk_id


async def _rebuild_stats(engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    """Recompute the BM25 statistics the way ingestion does.

    A direct insert does not go through the pipeline, so the test owns this step.
    Keeping it explicit is also what lets a test assert on the exact ``N``/``df``
    it intended rather than on whatever a parser happened to produce.
    """
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


async def _insert_course(
    engine: AsyncEngine, *, tenant_id: uuid.UUID, user_id: uuid.UUID, name: str
) -> uuid.UUID:
    course_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO courses (id, tenant_id, user_id, name) "
                "VALUES (:id, :tenant_id, :user_id, :name)"
            ),
            {
                "id": str(course_id),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "name": name,
            },
        )
    return course_id


async def _insert_document(
    engine: AsyncEngine,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    source_type: SourceType,
) -> uuid.UUID:
    document_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO documents (id, tenant_id, course_id, user_id, filename, "
                "storage_key, content_type, size_bytes, sha256, source_type) "
                "VALUES (:id, :tenant_id, :course_id, :user_id, :filename, :storage_key, "
                "'text/plain', 64, :sha256, :source_type)"
            ),
            {
                "id": str(document_id),
                "tenant_id": str(tenant_id),
                "course_id": str(course_id),
                "user_id": str(user_id),
                "filename": f"{uuid.uuid4().hex}.txt",
                "storage_key": uuid.uuid4().hex,
                "sha256": uuid.uuid4().hex + uuid.uuid4().hex,
                "source_type": source_type.value,
            },
        )
    return document_id


def _rare_versus_common_corpus() -> list[_Chunk]:
    """100 chunks: one rare term, 90 common ones, 9 filling the corpus."""
    chunks = [
        _Chunk(
            content="rareterm alpha beta gamma delta",
            terms={"rareterm": 1},
            token_count=10,
        )
    ]
    chunks.extend(
        _Chunk(
            content="commonterm alpha beta gamma delta",
            terms={"commonterm": 1},
            token_count=10,
        )
        for _ in range(90)
    )
    chunks.extend(
        _Chunk(
            content=f"filler{i} alpha beta gamma delta",
            terms={f"filler{i}": 1},
            token_count=10,
        )
        for i in range(9)
    )
    assert len(chunks) == 100
    return chunks


# ---------------------------------------------------------------------------
# Rare vs common
# ---------------------------------------------------------------------------
async def test_rare_term_outranks_a_common_term(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    ids = await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=_rare_versus_common_corpus(),
    )
    rare_id, common_ids = ids[0], set(ids[1:91])

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session,
            scope,
            pg_settings,
            query="rareterm commonterm",
            filters=RetrievalFilters(),
            k=20,
        )

    assert outcome.degraded == []
    assert outcome.results[0].chunk_id == rare_id
    assert outcome.results[0].rank == 1

    by_id = {result.chunk_id: result for result in outcome.results}
    common_scores = [by_id[cid].score for cid in common_ids if cid in by_id]
    assert common_scores, "the common term must also be retrievable"
    assert by_id[rare_id].score > max(common_scores)

    # The exact numbers, from the reference formula with N=100, df(rare)=1.
    expected_rare = bm25_score(
        query_terms=["rareterm", "commonterm"],
        term_frequencies={"rareterm": 1},
        token_count=10,
        doc_frequencies={"rareterm": 1, "commonterm": 90},
        doc_count=100,
        avg_doc_len=10.0,
    )
    assert by_id[rare_id].score == pytest.approx(round(expected_rare, 6), abs=1e-9)
    assert by_id[rare_id].score == pytest.approx(4.209655, abs=1e-6)


async def test_term_in_every_chunk_contributes_almost_nothing(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    chunks = [
        _Chunk(
            content="rareterm ubiquitous alpha",
            terms={"rareterm": 1, "ubiquitous": 1},
            token_count=10,
        )
    ]
    chunks.extend(
        _Chunk(content="ubiquitous alpha", terms={"ubiquitous": 1}, token_count=10)
        for _ in range(99)
    )
    ids = await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=chunks,
    )

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session,
            scope,
            pg_settings,
            query="rareterm ubiquitous",
            filters=RetrievalFilters(),
            k=10,
        )

    assert outcome.degraded == []
    assert outcome.results[0].chunk_id == ids[0]
    ubiquitous_only = [result for result in outcome.results if result.chunk_id != ids[0]]
    assert ubiquitous_only
    assert all(result.score < 0.01 for result in ubiquitous_only)

    expected_ubiquitous_only = bm25_score(
        query_terms=["rareterm", "ubiquitous"],
        term_frequencies={"ubiquitous": 1},
        token_count=10,
        doc_frequencies={"rareterm": 1, "ubiquitous": 100},
        doc_count=100,
        avg_doc_len=10.0,
    )
    assert ubiquitous_only[0].score == pytest.approx(round(expected_ubiquitous_only, 6), abs=1e-9)


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------
async def test_stopword_only_query_degrades_to_empty_not_everything(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=[_Chunk(content="attention mechanism", terms={"attention": 1}, token_count=10)],
    )

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session,
            scope,
            pg_settings,
            query="the and of",
            filters=RetrievalFilters(),
            k=10,
        )

    assert outcome.results == []
    assert outcome.degraded == ["empty_query_terms"]


async def test_query_with_no_analysable_terms_degrades(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=[_Chunk(content="attention", terms={"attention": 1}, token_count=10)],
    )

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session, scope, pg_settings, query="?!...", filters=RetrievalFilters(), k=10
        )

    assert outcome.results == []
    assert outcome.degraded == ["empty_query_terms"]


async def test_tenant_without_corpus_statistics_reports_no_corpus_stats(
    pg_settings: Settings, seeded
) -> None:
    """A tenant with no chunks has no statistics row; that is not an error."""
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session, scope, pg_settings, query="alpha", filters=RetrievalFilters(), k=10
        )

    assert outcome.results == []
    assert outcome.degraded == ["no_corpus_stats"]


# ---------------------------------------------------------------------------
# Stable ordering
# ---------------------------------------------------------------------------
async def test_identical_scores_produce_a_stable_order(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    chunks = [
        _Chunk(content="sameword alpha beta", terms={"sameword": 1}, token_count=5)
        for _ in range(3)
    ]
    ids = await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=chunks,
    )

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        first = await lexical_search(
            session, scope, pg_settings, query="sameword", filters=RetrievalFilters(), k=10
        )
        second = await lexical_search(
            session, scope, pg_settings, query="sameword", filters=RetrievalFilters(), k=10
        )

    first_ids = [result.chunk_id for result in first.results]
    second_ids = [result.chunk_id for result in second.results]

    assert len(first_ids) == 3
    assert first_ids == second_ids
    assert len({result.score for result in first.results}) == 1
    # The tie-break is ascending chunk id, so the order is reproducible.
    assert first_ids == sorted(ids)


# ---------------------------------------------------------------------------
# Metadata filters
# ---------------------------------------------------------------------------
async def test_metadata_filters_each_narrow_the_result_set(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    course_one = seeded.tenant_a.course_id
    document_one = seeded.tenant_a.document_id
    course_two = await _insert_course(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        user_id=seeded.user_a_id,
        name="Second Course",
    )
    document_two = await _insert_document(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=course_two,
        user_id=seeded.user_a_id,
        source_type=SourceType.LECTURE,
    )
    document_three = await _insert_document(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=course_one,
        user_id=seeded.user_a_id,
        source_type=SourceType.PAPER,
    )
    async with owner_engine.begin() as connection:
        await connection.execute(
            text("UPDATE documents SET source_type = 'notes' WHERE id = :id"),
            {"id": str(document_one)},
        )

    chunk_one = await _insert_chunk(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=course_one,
        document_id=document_one,
        chunk=_Chunk(
            content="alpha one", terms={"alpha": 1}, token_count=5, page=1, topic="attention"
        ),
        chunk_index=0,
    )
    chunk_two = await _insert_chunk(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=course_two,
        document_id=document_two,
        chunk=_Chunk(
            content="alpha two",
            terms={"alpha": 1},
            token_count=5,
            page=2,
            topic="optimisation",
        ),
        chunk_index=0,
    )
    chunk_three = await _insert_chunk(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=course_one,
        document_id=document_three,
        chunk=_Chunk(
            content="alpha three",
            terms={"alpha": 1},
            token_count=5,
            page=1,
            topic="attention",
        ),
        chunk_index=0,
    )
    await _rebuild_stats(owner_engine, seeded.tenant_a_id)

    scope = TenantScope(seeded.tenant_a_id)
    disabled = pg_settings.model_copy(update={"metadata_filtering_enabled": False})

    async def _search(filters: RetrievalFilters, settings: Settings = pg_settings):
        async with tenant_session(pg_settings, scope) as session:
            return await lexical_search(
                session, scope, settings, query="alpha", filters=filters, k=10
            )

    def _ids(outcome) -> set[uuid.UUID]:
        return {result.chunk_id for result in outcome.results}

    assert _ids(await _search(RetrievalFilters())) == {chunk_one, chunk_two, chunk_three}
    assert _ids(await _search(RetrievalFilters(course_id=course_one))) == {
        chunk_one,
        chunk_three,
    }
    assert _ids(await _search(RetrievalFilters(document_id=document_two))) == {chunk_two}
    assert _ids(await _search(RetrievalFilters(topic="attention"))) == {chunk_one, chunk_three}
    assert _ids(await _search(RetrievalFilters(page=2))) == {chunk_two}
    assert _ids(await _search(RetrievalFilters(source_types=[SourceType.LECTURE]))) == {chunk_two}
    assert _ids(
        await _search(RetrievalFilters(source_types=[SourceType.NOTES, SourceType.PAPER]))
    ) == {chunk_one, chunk_three}

    # Disabling metadata filtering drops the narrowing but never the tenant.
    assert _ids(await _search(RetrievalFilters(course_id=course_one), settings=disabled)) == {
        chunk_one,
        chunk_two,
        chunk_three,
    }


# ---------------------------------------------------------------------------
# SQL vs the pure-Python reference
# ---------------------------------------------------------------------------
async def test_sql_bm25_matches_the_python_reference(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    specs = [
        _Chunk(content="alpha alpha beta", terms={"alpha": 2, "beta": 1}, token_count=10),
        _Chunk(content="alpha", terms={"alpha": 1}, token_count=20),
        _Chunk(content="gamma gamma gamma", terms={"gamma": 3}, token_count=10),
        _Chunk(content="alpha gamma", terms={"alpha": 1, "gamma": 1}, token_count=30),
    ]
    ids = await _insert_corpus(
        owner_engine,
        tenant_id=seeded.tenant_a_id,
        course_id=seeded.tenant_a.course_id,
        document_id=seeded.tenant_a.document_id,
        chunks=specs,
    )
    doc_frequencies = {"alpha": 3, "beta": 1, "gamma": 2}
    doc_count = 4
    avg_doc_len = 17.5

    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        outcome = await lexical_search(
            session, scope, pg_settings, query="alpha gamma", filters=RetrievalFilters(), k=10
        )

    assert outcome.degraded == []
    by_id = {result.chunk_id: result for result in outcome.results}
    assert set(by_id) == set(ids)

    for chunk_id, spec in zip(ids, specs, strict=True):
        expected = bm25_score(
            query_terms=["alpha", "gamma"],
            term_frequencies=spec.terms,
            token_count=spec.token_count,
            doc_frequencies=doc_frequencies,
            doc_count=doc_count,
            avg_doc_len=avg_doc_len,
        )
        assert by_id[chunk_id].score == pytest.approx(round(expected, 6), abs=1e-9), spec
