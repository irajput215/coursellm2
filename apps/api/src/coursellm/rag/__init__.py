"""Retrieval-augmented generation.

This package owns the parts of the system that turn a stored document into
retrievable evidence and later turn a question into a ranked list of passages:

* :mod:`coursellm.rag.analyzers` — the single tokenisation contract shared by
  indexing and querying.
* :mod:`coursellm.rag.ingestion` — parse, chunk, embed and index a document.

Only the ingestion half exists today; fusion and reranking arrive with later
pull requests. The package is deliberately separate from ``services`` because
retrieval is a domain of its own and must not depend on the HTTP layer.
"""

from __future__ import annotations

__all__: list[str] = []
