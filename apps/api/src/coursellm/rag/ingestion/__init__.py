"""Ingestion package: parse a file, chunk it, embed it and index it.

The pipeline is deliberately a plain async function over an
:class:`~sqlalchemy.ext.asyncio.AsyncSession` rather than a class or a worker
framework object. Uploads and re-indexing jobs take the same path, and a test can
call it directly without standing up an HTTP server or a queue.
"""

from __future__ import annotations

from coursellm.rag.ingestion.chunker import (
    ChunkDraft,
    ChunkingConfig,
    chunk_pages,
    chunking_config_version,
)
from coursellm.rag.ingestion.embedders import (
    Embedder,
    HashingEmbedder,
    SentenceTransformerEmbedder,
    clear_embedder_cache,
    get_embedder,
)
from coursellm.rag.ingestion.parsers import (
    ParsedDocument,
    ParsedPage,
    parse_document,
    sniff_source_type,
)
from coursellm.rag.ingestion.pipeline import IngestionResult, ingest_document, reingest

__all__ = [
    "ChunkDraft",
    "ChunkingConfig",
    "Embedder",
    "HashingEmbedder",
    "IngestionResult",
    "ParsedDocument",
    "ParsedPage",
    "SentenceTransformerEmbedder",
    "chunk_pages",
    "chunking_config_version",
    "clear_embedder_cache",
    "get_embedder",
    "ingest_document",
    "parse_document",
    "reingest",
    "sniff_source_type",
]
