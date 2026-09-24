"""End-to-end ingestion tests against a real PostgreSQL instance.

These run as the ``coursellm_app`` role through :func:`tenant_session`, so every
statement is subject to Row-Level Security. Direct assertions about stored
``tenant_id`` values and about tenant B's view use the owner engine or a
second tenant session respectively — an isolation claim proven with the owner
role would prove nothing.
"""

from __future__ import annotations

import math

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from coursellm.core.config import Settings
from coursellm.db.models.content import EMBEDDING_DIM, DocumentStatus
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.rag.ingestion.embedders import HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document, reingest
from coursellm.repositories.content import DocumentRepository

pytestmark = pytest.mark.integration

# Small chunk sizes keep the fixture text short while still producing many
# chunks, so term frequencies repeat across chunk boundaries.
_CHUNK_OVERRIDES: dict[str, int] = {
    "chunk_size_tokens": 24,
    "chunk_overlap_tokens": 6,
    "min_chunk_tokens": 1,
}


def _ingest_settings(settings: Settings) -> Settings:
    return settings.model_copy(update=_CHUNK_OVERRIDES)


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


class _ExplodingEmbedder:
    """Fails at embed time so the failure path can be exercised."""

    model_id = "exploding-v1"
    dim = EMBEDDING_DIM

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding backend exploded")

    async def embed_query(self, text: str) -> list[float]:
        raise RuntimeError("embedding backend exploded")


async def _ingest_for_tenant_a(
    pg_settings: Settings, seeded, *, data: bytes
) -> tuple[Settings, TenantScope, object]:
    ingest_settings = _ingest_settings(pg_settings)
    scope = TenantScope(seeded.tenant_a_id)
    async with tenant_session(pg_settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        result = await ingest_document(
            session,
            ingest_settings,
            document=document,
            data=data,
            embedder=HashingEmbedder(dim=EMBEDDING_DIM),
        )
        assert document.status is DocumentStatus.READY
        assert document.page_count == 1
        assert document.chunking_config_version == result.chunking_config_version
    return ingest_settings, scope, result


async def test_ingest_writes_tenant_scoped_chunks_embeddings_and_terms(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    _, _, result = await _ingest_for_tenant_a(pg_settings, seeded, data=data)

    assert result.chunk_count > 1
    assert result.token_count > 0

    async with owner_engine.connect() as connection:
        chunk_rows = (
            (
                await connection.execute(
                    text(
                        "SELECT id, tenant_id, chunk_index, token_count, page "
                        "FROM chunks WHERE document_id = :document_id ORDER BY chunk_index"
                    ),
                    {"document_id": str(seeded.tenant_a.document_id)},
                )
            )
            .mappings()
            .all()
        )
        assert len(chunk_rows) == result.chunk_count
        assert [row["chunk_index"] for row in chunk_rows] == list(range(result.chunk_count))
        assert all(row["tenant_id"] == seeded.tenant_a_id for row in chunk_rows)
        assert all(row["token_count"] > 0 for row in chunk_rows)
        assert all(row["page"] == 1 for row in chunk_rows)

        embedding_rows = (
            (
                await connection.execute(
                    text(
                        "SELECT e.tenant_id, e.embedding_model, e.dim "
                        "FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id "
                        "WHERE c.document_id = :document_id"
                    ),
                    {"document_id": str(seeded.tenant_a.document_id)},
                )
            )
            .mappings()
            .all()
        )
        assert len(embedding_rows) == result.chunk_count
        assert all(row["tenant_id"] == seeded.tenant_a_id for row in embedding_rows)
        assert all(row["embedding_model"] == result.embedding_model for row in embedding_rows)
        assert all(row["dim"] == EMBEDDING_DIM for row in embedding_rows)

        term_rows = (
            (
                await connection.execute(
                    text(
                        "SELECT t.tenant_id, t.term, t.tf "
                        "FROM chunk_terms t JOIN chunks c ON c.id = t.chunk_id "
                        "WHERE c.document_id = :document_id"
                    ),
                    {"document_id": str(seeded.tenant_a.document_id)},
                )
            )
            .mappings()
            .all()
        )
        assert term_rows
        assert all(row["tenant_id"] == seeded.tenant_a_id for row in term_rows)
        assert all(row["tf"] > 0 for row in term_rows)


async def test_document_row_is_ready_with_ingestion_metadata(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    _, _, result = await _ingest_for_tenant_a(pg_settings, seeded, data=data)

    async with owner_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT status, error_message, page_count, chunking_config_version "
                        "FROM documents WHERE id = :document_id"
                    ),
                    {"document_id": str(seeded.tenant_a.document_id)},
                )
            )
            .mappings()
            .one()
        )

    assert row["status"] == DocumentStatus.READY.value
    assert row["error_message"] is None
    assert row["page_count"] == 1
    assert row["chunking_config_version"] == result.chunking_config_version


async def test_lexical_statistics_match_chunk_terms(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    await _ingest_for_tenant_a(pg_settings, seeded, data=data)

    async with owner_engine.connect() as connection:
        expected = {
            str(term): int(doc_freq)
            for term, doc_freq in (
                await connection.execute(
                    text(
                        "SELECT term, count(DISTINCT chunk_id) AS doc_freq FROM chunk_terms "
                        "WHERE tenant_id = :tenant_id GROUP BY term"
                    ),
                    {"tenant_id": str(seeded.tenant_a_id)},
                )
            ).all()
        }
        stored = {
            str(term): int(doc_freq)
            for term, doc_freq in (
                await connection.execute(
                    text(
                        "SELECT term, doc_freq FROM tenant_lexical_stats "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(seeded.tenant_a_id)},
                )
            ).all()
        }

    assert expected
    # The assertion is only meaningful if a term actually spans several chunks.
    assert max(expected.values()) >= 2
    assert stored == expected
    assert all(doc_freq > 0 for doc_freq in stored.values())


async def test_corpus_statistics_are_the_tenant_chunk_aggregate(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    result = (await _ingest_for_tenant_a(pg_settings, seeded, data=data))[2]

    async with owner_engine.connect() as connection:
        chunk_count, total_tokens = (
            await connection.execute(
                text(
                    "SELECT count(*), coalesce(sum(token_count), 0) FROM chunks "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": str(seeded.tenant_a_id)},
            )
        ).one()
        row = (
            (
                await connection.execute(
                    text(
                        "SELECT doc_count, total_tokens, avg_doc_len FROM tenant_corpus_stats "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(seeded.tenant_a_id)},
                )
            )
            .mappings()
            .one()
        )

    assert int(chunk_count) == result.chunk_count
    assert int(row["doc_count"]) == int(chunk_count)
    assert int(row["total_tokens"]) == int(total_tokens)
    assert int(total_tokens) == result.token_count
    assert float(row["avg_doc_len"]) == pytest.approx(int(total_tokens) / int(chunk_count))
    assert math.isfinite(float(row["avg_doc_len"]))


async def test_reingest_replaces_chunks_without_inflating_statistics(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    ingest_settings, scope, first = await _ingest_for_tenant_a(pg_settings, seeded, data=data)

    async with owner_engine.connect() as connection:
        first_rows = (
            await connection.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :tenant_id"),
                {"tenant_id": str(seeded.tenant_a_id)},
            )
        ).scalar_one()
        first_stats = dict(
            (
                await connection.execute(
                    text(
                        "SELECT term, doc_freq FROM tenant_lexical_stats "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(seeded.tenant_a_id)},
                )
            ).all()
        )

    async with tenant_session(pg_settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        second = await reingest(
            session,
            ingest_settings,
            document=document,
            data=data,
            embedder=HashingEmbedder(dim=EMBEDDING_DIM),
        )

    async with owner_engine.connect() as connection:
        second_rows = (
            await connection.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :tenant_id"),
                {"tenant_id": str(seeded.tenant_a_id)},
            )
        ).scalar_one()
        second_stats = dict(
            (
                await connection.execute(
                    text(
                        "SELECT term, doc_freq FROM tenant_lexical_stats "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": str(seeded.tenant_a_id)},
                )
            ).all()
        )

    assert int(second_rows) == int(first_rows) == first.chunk_count == second.chunk_count
    assert second_stats == first_stats


async def test_tenant_b_cannot_see_tenant_a_ingestion_rows(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    data = _document_text()
    await _ingest_for_tenant_a(pg_settings, seeded, data=data)

    # Proof the owner role really does see tenant A's rows; otherwise the empty
    # result below would be evidence of a missing fixture, not of isolation.
    async with owner_engine.connect() as connection:
        assert (
            await connection.execute(
                text("SELECT count(*) FROM chunks WHERE tenant_id = :tenant_id"),
                {"tenant_id": str(seeded.tenant_a_id)},
            )
        ).scalar_one() > 0

    statements = (
        ("chunks", text("SELECT count(*) FROM chunks")),
        ("chunk_embeddings", text("SELECT count(*) FROM chunk_embeddings")),
        ("chunk_terms", text("SELECT count(*) FROM chunk_terms")),
        ("tenant_lexical_stats", text("SELECT count(*) FROM tenant_lexical_stats")),
        ("tenant_corpus_stats", text("SELECT count(*) FROM tenant_corpus_stats")),
    )
    async with tenant_session(pg_settings, TenantScope(seeded.tenant_b_id)) as session:
        for label, statement in statements:
            count = (await session.execute(statement)).scalar_one()
            assert count == 0, f"tenant B saw {count} rows in {label}"


async def test_failure_marks_the_document_failed(
    pg_settings: Settings, seeded, owner_engine: AsyncEngine
) -> None:
    ingest_settings = _ingest_settings(pg_settings)
    scope = TenantScope(seeded.tenant_a_id)

    async with tenant_session(pg_settings, scope) as session:
        document = await DocumentRepository(session, scope).get_or_raise(
            seeded.tenant_a.document_id
        )
        # The exception is caught inside the session block so the transaction
        # commits the FAILED status; the pipeline must never leave a document
        # READY after a failed ingest.
        with pytest.raises(RuntimeError):
            await ingest_document(
                session,
                ingest_settings,
                document=document,
                data=_document_text(),
                embedder=_ExplodingEmbedder(),
            )

    async with owner_engine.connect() as connection:
        row = (
            (
                await connection.execute(
                    text("SELECT status, error_message FROM documents WHERE id = :document_id"),
                    {"document_id": str(seeded.tenant_a.document_id)},
                )
            )
            .mappings()
            .one()
        )

    assert row["status"] == DocumentStatus.FAILED.value
    assert row["error_message"]
