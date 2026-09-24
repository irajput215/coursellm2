"""The ingestion pipeline: bytes in, retrievable chunks out.

The pipeline is one async function over an existing
:class:`~sqlalchemy.ext.asyncio.AsyncSession`. It never commits: the caller owns
the transaction boundary, exactly as the repository layer does, so a failed
ingest rolls back atomically instead of leaving a half-indexed document.

## Idempotency

Re-ingesting a document must replace its content, not append to it. The pipeline
therefore deletes the document's existing chunks — the foreign keys cascade to
``chunk_embeddings`` and ``chunk_terms`` — before writing the new ones. Without
that, re-uploading the same file would double every retrieval result and inflate
document frequencies, which is the failure the dedup-by-sha256 design exists to
prevent.

## Incremental lexical statistics: recompute the affected terms

``tenant_lexical_stats.doc_freq`` counts, per tenant, the chunks containing a
term. A delete-plus-insert cannot be maintained by incrementing and
decrementing alone: a term may be removed from one document and simultaneously
re-added by the replacement chunks, and any ordering of ``+1``/``-1`` around
that is easy to get subtly wrong and impossible to notice.

The chosen strategy is **recompute, for the union of the old and new documents'
terms**, ``doc_freq`` with a single grouped ``COUNT(DISTINCT chunk_id)`` over
``chunk_terms``. It is correct by construction on every re-ingest, and its cost
is bounded by the document's own vocabulary rather than by the tenant's full
term list — a document touches only the terms it contains, so a re-ingest of one
file never rescans the corpus. The alternative of recomputing the whole tenant's
statistics is simpler but grows with the tenant and would turn a single-file
re-upload into an O(corpus) operation; this is the middle ground.

``tenant_corpus_stats`` *is* recomputed for the whole tenant, because it is a
single aggregate row (``count`` / ``sum`` over the tenant's chunks) and there is
no bounded way to derive it from a per-document delta that survives deletion.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.core.errors import ValidationError
from coursellm.core.logging import get_logger
from coursellm.db.models.content import (
    Chunk,
    ChunkEmbedding,
    ChunkTerm,
    Document,
    DocumentStatus,
    QuarantineState,
    TenantCorpusStats,
    TenantLexicalStats,
)
from coursellm.db.tenancy import set_tenant_guc
from coursellm.rag.analyzers import term_frequencies, tokenize_for_index
from coursellm.rag.ingestion.chunker import ChunkingConfig, chunk_pages, chunking_config_version
from coursellm.rag.ingestion.embedders import Embedder, get_embedder
from coursellm.rag.ingestion.parsers import parse_document
from coursellm.security.injection import classify
from coursellm.security.sanitize import scrub_for_storage

logger = get_logger(__name__)

# ``error_message`` is free text surfaced to the uploader; it is truncated so a
# provider stack trace cannot bloat the row (or a response body).
_MAX_ERROR_MESSAGE_CHARS = 2000


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """What an ingest produced, for the caller to report or log."""

    document_id: uuid.UUID
    chunk_count: int
    token_count: int
    page_count: int
    embedding_model: str
    chunking_config_version: str
    warnings: tuple[str, ...] = ()


async def ingest_document(
    session: AsyncSession,
    settings: Settings,
    *,
    document: Document,
    data: bytes,
    embedder: Embedder | None = None,
    quarantine_enabled: bool = True,
) -> IngestionResult:
    """Parse, chunk, embed and index ``data`` as ``document``.

    The passed ``document`` is the tenant authority: its ``tenant_id`` is
    re-asserted as the transaction's tenancy variable, so the row-level policies
    apply to every statement this function issues even if the caller's session
    was scoped differently. Re-ingesting the same document is safe and replaces
    its chunks rather than adding to them.

    ``quarantine_enabled`` is the trusted-corpus switch. A user upload always
    quarantines (the default). A curated, version-controlled corpus — the
    evaluation corpus is the only caller that passes ``False`` — records the
    detector score and classes on the document but never withholds its chunks,
    because reference material *about* prompt injection legitimately contains
    attack examples and a blunt quarantine would remove the very evidence the
    corpus exists to provide.
    """
    resolved_embedder = embedder if embedder is not None else get_embedder(settings)
    tenant_id = document.tenant_id
    await set_tenant_guc(session, tenant_id)

    document.error_message = None
    try:
        document.status = DocumentStatus.PARSING
        await session.flush()

        parsed = parse_document(
            data,
            filename=document.filename,
            content_type=document.content_type,
            settings=settings,
        )
        config = ChunkingConfig.from_settings(settings)
        drafts = chunk_pages(parsed.pages, config)
        if not drafts:
            # A READY document with no chunks is retrievable by nothing while
            # reporting success, which is worse than a visible failure.
            raise ValidationError(
                "The document produced no chunks (it may contain no extractable text)."
            )

        # Neutralise reserved markers at ingest as belt-and-braces: assembly is
        # the only place the fence is built and therefore the only place that has
        # to strip, but a future consumer that renders stored passages directly
        # is protected by doing it here too.
        contents = [scrub_for_storage(draft.content) for draft in drafts]
        verdicts = [classify(content, settings=settings) for content in contents]
        max_score = max((verdict.score for verdict in verdicts), default=0.0)
        classes = sorted({name for verdict in verdicts for name in verdict.classes})
        quarantine = _quarantine_state(max_score, settings)
        if not quarantine_enabled and quarantine is QuarantineState.QUARANTINED:
            # A trusted corpus records the signal but stays retrievable.
            quarantine = QuarantineState.FLAGGED
        document.injection_score = max_score
        document.injection_classes = classes
        document.quarantine_state = quarantine
        await session.flush()

        if quarantine is QuarantineState.QUARANTINED:
            # Quarantined content is excluded from retrieval entirely: no chunk
            # is written, so no retriever can return it even by a code path that
            # forgets the predicate. The document row remains so a reviewer can
            # inspect and clear it; clearing re-ingests through this same path.
            stale_terms = await _terms_for_document(session, document.id, tenant_id)
            await _delete_existing_chunks(session, document_id=document.id, tenant_id=tenant_id)
            await _recompute_lexical_stats(session, tenant_id=tenant_id, terms=stale_terms)
            await _recompute_corpus_stats(session, tenant_id=tenant_id)
            document.status = DocumentStatus.READY
            document.page_count = parsed.page_count
            document.chunking_config_version = chunking_config_version(config)
            await session.flush()
            logger.warning(
                "document_quarantined",
                document_id=str(document.id),
                tenant_id=str(tenant_id),
                injection_score=max_score,
                injection_classes=classes,
            )
            return IngestionResult(
                document_id=document.id,
                chunk_count=0,
                token_count=sum(draft.token_count for draft in drafts),
                page_count=parsed.page_count,
                embedding_model=resolved_embedder.model_id,
                chunking_config_version=chunking_config_version(config),
                warnings=(*parsed.warnings, "document_quarantined"),
            )

        document.status = DocumentStatus.EMBEDDING
        await session.flush()
        vectors = await _embed_in_batches(resolved_embedder, contents, settings)
        _validate_vectors(vectors, settings, resolved_embedder.model_id)

        # Collect the terms the old chunks contributed *before* deleting them;
        # they are half of the affected set for the statistics recomputation.
        affected_terms = await _terms_for_document(session, document.id, tenant_id)
        await _delete_existing_chunks(session, document_id=document.id, tenant_id=tenant_id)

        document.status = DocumentStatus.INDEXING
        await session.flush()

        chunks: list[Chunk] = [
            Chunk(
                tenant_id=tenant_id,
                document_id=document.id,
                course_id=document.course_id,
                content=content,
                page=draft.page,
                chunk_index=draft.chunk_index,
                token_count=draft.token_count,
                starts_mid_sentence=draft.starts_mid_sentence,
            )
            for draft, content in zip(drafts, contents, strict=True)
        ]
        session.add_all(chunks)
        await session.flush()  # assign chunk ids for the child rows

        session.add_all(
            [
                ChunkEmbedding(
                    tenant_id=tenant_id,
                    chunk_id=chunk.id,
                    embedding_model=resolved_embedder.model_id,
                    dim=resolved_embedder.dim,
                    embedding=vector,
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )

        for chunk, content in zip(chunks, contents, strict=True):
            frequencies = term_frequencies(tokenize_for_index(content))
            affected_terms.update(frequencies)
            session.add_all(
                [
                    ChunkTerm(chunk_id=chunk.id, tenant_id=tenant_id, term=term, tf=tf)
                    for term, tf in frequencies.items()
                ]
            )
        await session.flush()

        await _recompute_lexical_stats(session, tenant_id=tenant_id, terms=affected_terms)
        await _recompute_corpus_stats(session, tenant_id=tenant_id)

        document.status = DocumentStatus.READY
        document.page_count = parsed.page_count
        document.chunking_config_version = chunking_config_version(config)
        document.error_message = None
        await session.flush()
    except Exception as exc:
        document.status = DocumentStatus.FAILED
        document.error_message = str(exc)[:_MAX_ERROR_MESSAGE_CHARS] or type(exc).__name__
        try:
            await session.flush()
        except Exception:
            # The transaction may already be aborted (an IntegrityError does
            # this), in which case the status update cannot be written until the
            # caller rolls back. That is logged rather than raised so the real
            # cause reaches the caller.
            logger.warning(
                "ingestion_failure_status_not_persisted",
                document_id=str(document.id),
                exc_type=type(exc).__name__,
            )
        raise

    logger.info(
        "document_ingested",
        document_id=str(document.id),
        tenant_id=str(tenant_id),
        chunk_count=len(chunks),
        page_count=parsed.page_count,
    )
    return IngestionResult(
        document_id=document.id,
        chunk_count=len(chunks),
        token_count=sum(draft.token_count for draft in drafts),
        page_count=parsed.page_count,
        embedding_model=resolved_embedder.model_id,
        chunking_config_version=chunking_config_version(config),
        warnings=tuple(parsed.warnings),
    )


async def reingest(
    session: AsyncSession,
    settings: Settings,
    *,
    document: Document,
    data: bytes,
    embedder: Embedder | None = None,
    quarantine_enabled: bool = True,
) -> IngestionResult:
    """Re-index a document, replacing its existing chunks.

    Deliberately identical to :func:`ingest_document`: idempotency is a property
    of the pipeline rather than of a separate code path, so there is no second
    implementation that could drift from the first. It exists as a named entry
    point because "re-ingest this document" is a distinct operation for callers
    and logs, not because it does anything different.
    """
    return await ingest_document(
        session,
        settings,
        document=document,
        data=data,
        embedder=embedder,
        quarantine_enabled=quarantine_enabled,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _quarantine_state(score: float, settings: Settings) -> QuarantineState:
    """Map a document's maximum detector score to a review state.

    The thresholds are inclusive, exactly as for a query verdict: a document at
    the block threshold is quarantined, and one at the warn threshold is flagged.
    """
    if score >= settings.injection_block_threshold:
        return QuarantineState.QUARANTINED
    if score >= settings.injection_warn_threshold:
        return QuarantineState.FLAGGED
    return QuarantineState.CLEAN


async def _embed_in_batches(
    embedder: Embedder, texts: list[str], settings: Settings
) -> list[list[float]]:
    """Embed in ``embedding_batch_size`` slices.

    The embedder may batch internally, but slicing here bounds peak memory
    regardless of provider and keeps a large upload from being handed to a naive
    implementation in one call.
    """
    batch_size = max(1, settings.embedding_batch_size)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(await embedder.embed_documents(texts[start : start + batch_size]))
    return vectors


def _validate_vectors(vectors: list[list[float]], settings: Settings, model_id: str) -> None:
    for vector in vectors:
        if len(vector) != settings.embedding_dim:
            raise ValidationError(
                f"Embedder {model_id!r} returned a {len(vector)}-dimensional vector but "
                f"embedding_dim is {settings.embedding_dim}."
            )


async def _terms_for_document(
    session: AsyncSession, document_id: uuid.UUID, tenant_id: uuid.UUID
) -> set[str]:
    statement = (
        select(ChunkTerm.term)
        .join(Chunk, Chunk.id == ChunkTerm.chunk_id)
        .where(ChunkTerm.tenant_id == tenant_id, Chunk.document_id == document_id)
        .distinct()
    )
    result = await session.execute(statement)
    return set(result.scalars().all())


async def _delete_existing_chunks(
    session: AsyncSession, *, document_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    """Delete a document's chunks; cascades clear embeddings and terms.

    A Core delete is used rather than an ORM cascade so the database does the
    work in one statement instead of loading every chunk into the session.
    """
    await session.execute(
        delete(Chunk).where(Chunk.document_id == document_id, Chunk.tenant_id == tenant_id)
    )


async def _recompute_lexical_stats(
    session: AsyncSession, *, tenant_id: uuid.UUID, terms: set[str]
) -> None:
    """Recompute ``doc_freq`` for exactly the given terms.

    Terms that no longer occur anywhere are deleted rather than left at zero,
    because ``tenant_lexical_stats`` has a ``doc_freq > 0`` check constraint and
    a zero row would make IDF wrong in the other direction (a term that exists
    globally but not in this tenant must not appear in this tenant's statistics
    at all).
    """
    if not terms:
        return

    frequencies = (
        select(
            ChunkTerm.term,
            func.count(func.distinct(ChunkTerm.chunk_id)).label("doc_freq"),
        )
        .where(ChunkTerm.tenant_id == tenant_id, ChunkTerm.term.in_(terms))
        .group_by(ChunkTerm.term)
    )
    rows = (await session.execute(frequencies)).all()
    current: dict[str, int] = {str(term): int(doc_freq) for term, doc_freq in rows}

    if current:
        upsert: Any = pg_insert(TenantLexicalStats).values(
            [
                {"tenant_id": tenant_id, "term": term, "doc_freq": doc_freq}
                for term, doc_freq in current.items()
            ]
        )
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=["tenant_id", "term"],
                set_={"doc_freq": upsert.excluded.doc_freq},
            )
        )

    stale = terms - current.keys()
    if stale:
        await session.execute(
            delete(TenantLexicalStats).where(
                TenantLexicalStats.tenant_id == tenant_id,
                TenantLexicalStats.term.in_(stale),
            )
        )


async def _recompute_corpus_stats(session: AsyncSession, *, tenant_id: uuid.UUID) -> None:
    """Recompute the tenant's BM25 corpus constants from its chunks.

    ``doc_count`` is the number of chunks, not the number of documents: BM25's
    ``N`` is the size of the candidate set that retrieval scores, which is the
    chunk set. ``avg_doc_len`` is the mean ``token_count`` over that set.
    """
    statement = select(
        func.count(Chunk.id),
        func.coalesce(func.sum(Chunk.token_count), 0),
    ).where(Chunk.tenant_id == tenant_id)
    doc_count, total_tokens = (await session.execute(statement)).one()
    doc_count = int(doc_count)
    total_tokens = int(total_tokens)
    avg_doc_len = total_tokens / doc_count if doc_count else 0.0

    upsert: Any = pg_insert(TenantCorpusStats).values(
        [
            {
                "tenant_id": tenant_id,
                "doc_count": doc_count,
                "total_tokens": total_tokens,
                "avg_doc_len": avg_doc_len,
            }
        ]
    )
    await session.execute(
        upsert.on_conflict_do_update(
            index_elements=["tenant_id"],
            set_={
                "doc_count": upsert.excluded.doc_count,
                "total_tokens": upsert.excluded.total_tokens,
                "avg_doc_len": upsert.excluded.avg_doc_len,
                "updated_at": func.now(),
            },
        )
    )
