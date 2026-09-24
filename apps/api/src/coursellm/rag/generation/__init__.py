"""Grounded generation: context assembly, citations and guarded answering.

The pipeline is a pure-ish sequence over the ranked passages from
:mod:`coursellm.rag.rerank`:

* :func:`~coursellm.rag.generation.context.assemble` packs passages into a
  token-budgeted, delimited untrusted-evidence region and assigns citation ids.
* :mod:`coursellm.rag.generation.citations` extracts and verifies the ids an
  answer cites, and strips any that do not exist.
* :class:`~coursellm.rag.generation.generator.AnswerGenerator` prompts the
  gateway, verifies the result and degrades to an extractive answer when the
  model is unreachable.

Nothing here reaches a provider directly: every model call goes through
``coursellm.llm`` (ADR-0007).
"""

from __future__ import annotations

from coursellm.rag.generation.citations import (
    CitationReport,
    extract_citation_ids,
    is_refusal,
    strip_hallucinated,
    verify_citations,
)
from coursellm.rag.generation.context import (
    EVIDENCE_CLOSE_TAG,
    EVIDENCE_OPEN_TAG,
    AssembledContext,
    ChunkPosition,
    Citation,
    ContextPassage,
    DocumentMeta,
    assemble,
)
from coursellm.rag.generation.generator import (
    LLM_UNAVAILABLE,
    NO_EVIDENCE,
    UNCITED_ANSWER,
    AnswerGenerator,
    CompletionEvent,
    GeneratedAnswer,
    StreamEvent,
    TokenEvent,
    compose_extractive,
)

__all__ = [
    "EVIDENCE_CLOSE_TAG",
    "EVIDENCE_OPEN_TAG",
    "LLM_UNAVAILABLE",
    "NO_EVIDENCE",
    "UNCITED_ANSWER",
    "AnswerGenerator",
    "AssembledContext",
    "ChunkPosition",
    "Citation",
    "CitationReport",
    "CompletionEvent",
    "ContextPassage",
    "DocumentMeta",
    "GeneratedAnswer",
    "StreamEvent",
    "TokenEvent",
    "assemble",
    "compose_extractive",
    "extract_citation_ids",
    "is_refusal",
    "strip_hallucinated",
    "verify_citations",
]
