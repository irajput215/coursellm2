"""Token-budgeted context assembly and the delimited untrusted-evidence region.

Two invariants shape this module.

**The budget is a ceiling, never a target to round up.** Passages are packed in
rank order and the rendered region is re-counted on every addition, so the count
that is stored is the count of the text that is actually sent. The token counter
is injected rather than imported so that assembly and the gateway's accounting
use the same implementation (``coursellm.llm.cost.count_tokens``).

**Document text is data, never instruction.** Every passage is wrapped in an
``<untrusted_evidence>`` region whose attributes carry the citation id, the
source filename and the page. Any reserved marker in the passage text is
neutralised first, so a document cannot close the fence early and escape into
instruction position (``docs/architecture/security.md`` section 3.1).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from coursellm.core.config import Settings
from coursellm.db.models.content import SourceType
from coursellm.rag.rerank.pipeline import RankedPassage
from coursellm.security.sanitize import neutralise_markers

#: The opening delimiter of the untrusted region. The rendered form adds the
#: ``id``, ``source``, optional ``page`` and ``source_type`` attributes.
EVIDENCE_OPEN_TAG = "<untrusted_evidence"
#: The closing delimiter of the untrusted region.
EVIDENCE_CLOSE_TAG = "</untrusted_evidence>"

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

# One document may not take the whole context. The cap is applied both when
# merging adjacent chunks and when accepting further passages from the same
# document, so a single chatty file cannot crowd every other source out.
_DOCUMENT_BUDGET_FRACTION = 0.6
# The longest word overlap removed when stitching two adjacent chunks together.
_MAX_SEAM_WORDS = 40
# How much of a passage is stored as the citation's quoted span.
_QUOTE_CHARS = 280


class Citation(BaseModel):
    """A citation as displayed and persisted with an answer.

    ``quote`` is a short span of the cited passage for the reader's benefit. It
    is deliberately part of the persisted record but not of the API response
    schema, which does not publish evidence content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None = None
    source_type: SourceType
    quote: str


@dataclass(frozen=True, slots=True)
class ContextPassage:
    """One packed passage, with the citation id the model must use for it."""

    citation_id: str
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    page: int | None
    source_type: SourceType
    content: str
    token_count: int
    rerank_score: float | None


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """The evidence region, its citations and the accounting for it."""

    passages: list[ContextPassage]
    prompt_text: str
    citations: list[Citation]
    total_tokens: int
    truncated: bool
    dropped: int


@dataclass(frozen=True, slots=True)
class DocumentMeta:
    """The document metadata a citation needs, without the ORM object.

    Keeping the assembler free of the ORM is what lets it be unit-tested with
    hand-built values and no database.
    """

    document_id: uuid.UUID
    filename: str
    source_type: SourceType
    content_type: str | None = None
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkPosition:
    """Where a chunk sits inside its document.

    ``SearchResult`` does not carry the chunk index, so adjacency cannot be
    inferred from the ranked results alone. The caller that has the session (the
    chat service) loads these alongside the documents and passes them in; when
    they are absent the assembler simply does not merge.
    """

    index: int
    starts_mid_sentence: bool = False


def assemble(
    results: Sequence[RankedPassage],
    *,
    documents_by_id: Mapping[uuid.UUID, DocumentMeta],
    settings: Settings,
    token_counter: Callable[[str], int],
    chunk_positions: Mapping[uuid.UUID, ChunkPosition] | None = None,
) -> AssembledContext:
    """Pack ranked passages into a budgeted, delimited evidence region.

    ``results`` are consumed in ascending ``final_rank``. Adjacent chunks from
    the same document and page are merged when their indices are consecutive or
    when the later chunk starts mid-sentence, so the model reads continuous prose
    rather than a duplicated seam. Citation ids are assigned ``S1``, ``S2``, ...
    in the accepted order and are always contiguous.
    """
    ordered = sorted(results, key=lambda result: result.final_rank)
    budget = settings.context_token_budget
    document_cap = max(1, int(budget * _DOCUMENT_BUDGET_FRACTION))
    groups = _group(
        ordered,
        chunk_positions=chunk_positions,
        token_counter=token_counter,
        document_cap=document_cap,
    )

    blocks: list[str] = []
    passages: list[ContextPassage] = []
    citations: list[Citation] = []
    document_usage: dict[uuid.UUID, int] = {}
    included_chunks = 0
    truncated = False
    next_citation = 1

    for group in groups:
        head = group[0]
        meta = documents_by_id.get(head.source.document_id)
        if meta is None:
            continue

        page = head.source.page
        citation_id = f"S{next_citation}"
        content = _neutralise(_merged_content(group))
        document_remaining = document_cap - document_usage.get(meta.document_id, 0)

        if passages:
            block = _render_block(citation_id, meta, page, content)
            projected = token_counter("\n\n".join([*blocks, block]))
            if projected > budget or token_counter(block) > document_remaining:
                continue
        else:
            fitted, was_truncated = _fit_first_group(
                content=content,
                citation_id=citation_id,
                meta=meta,
                page=page,
                limit=min(budget, document_remaining),
                token_counter=token_counter,
            )
            if fitted is None:
                continue
            content = fitted
            truncated = truncated or was_truncated
            block = _render_block(citation_id, meta, page, content)

        blocks.append(block)
        document_usage[meta.document_id] = document_usage.get(meta.document_id, 0) + token_counter(
            block
        )
        included_chunks += len(group)
        passages.append(
            ContextPassage(
                citation_id=citation_id,
                chunk_id=head.chunk_id,
                document_id=meta.document_id,
                filename=meta.filename,
                page=page,
                source_type=meta.source_type,
                content=content,
                token_count=token_counter(content),
                rerank_score=head.rerank_score,
            )
        )
        citations.append(
            Citation(
                citation_id=citation_id,
                chunk_id=head.chunk_id,
                document_id=meta.document_id,
                filename=meta.filename,
                page=page,
                source_type=meta.source_type,
                quote=content[:_QUOTE_CHARS],
            )
        )
        next_citation += 1

    prompt_text = "\n\n".join(blocks)
    return AssembledContext(
        passages=passages,
        prompt_text=prompt_text,
        citations=citations,
        total_tokens=token_counter(prompt_text),
        truncated=truncated,
        dropped=len(ordered) - included_chunks,
    )


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------
def _group(
    ordered: Sequence[RankedPassage],
    *,
    chunk_positions: Mapping[uuid.UUID, ChunkPosition] | None,
    token_counter: Callable[[str], int],
    document_cap: int,
) -> list[list[RankedPassage]]:
    """Collect consecutive, adjacent passages into merge groups."""
    groups: list[list[RankedPassage]] = []
    for result in ordered:
        if groups and _can_merge(groups[-1][-1], result, chunk_positions):
            merged_tokens = token_counter(_merged_content(groups[-1])) + token_counter(
                result.source.content
            )
            if merged_tokens <= document_cap:
                groups[-1].append(result)
                continue
        groups.append([result])
    return groups


def _can_merge(
    tail: RankedPassage,
    following: RankedPassage,
    positions: Mapping[uuid.UUID, ChunkPosition] | None,
) -> bool:
    """Whether ``following`` continues the prose of ``tail``."""
    if tail.source.document_id != following.source.document_id:
        return False
    # Chunks never span a page boundary, so consecutive indices across a page
    # break would produce a citation with no single honest page number.
    if tail.source.page != following.source.page:
        return False
    if positions is None:
        return False
    following_position = positions.get(following.chunk_id)
    if following_position is None:
        return False
    if following_position.starts_mid_sentence:
        return True
    tail_position = positions.get(tail.chunk_id)
    if tail_position is None:
        return False
    return following_position.index == tail_position.index + 1


def _merged_content(group: Sequence[RankedPassage]) -> str:
    content = group[0].source.content
    for result in group[1:]:
        content = _join_seam(content, result.source.content)
    return content


def _join_seam(previous: str, following: str) -> str:
    """Join two chunks, dropping the overlap the chunker deliberately repeated.

    The ingestion chunker carries a suffix of analysed terms into the next chunk,
    so a naive concatenation shows the model the same terms twice. The longest
    word overlap (bounded, case- and punctuation-insensitive) is removed.
    """
    previous_words = previous.split()
    following_words = following.split()
    limit = min(len(previous_words), len(following_words), _MAX_SEAM_WORDS)
    for size in range(limit, 0, -1):
        if _normalised(previous_words[-size:]) == _normalised(following_words[:size]):
            remainder = " ".join(following_words[size:]).strip()
            return f"{previous} {remainder}".strip() if remainder else previous
    return f"{previous} {following}".strip()


def _normalised(words: Sequence[str]) -> tuple[str, ...]:
    return tuple(re.sub(r"\W+", "", word).lower() for word in words)


# ---------------------------------------------------------------------------
# Budget fitting
# ---------------------------------------------------------------------------
def _fit_first_group(
    *,
    content: str,
    citation_id: str,
    meta: DocumentMeta,
    page: int | None,
    limit: int,
    token_counter: Callable[[str], int],
) -> tuple[str | None, bool]:
    """Return the first group's content, truncating it if it alone exceeds ``limit``.

    Dropping it would leave an empty context even though there is evidence, so an
    oversized first passage is trimmed on a sentence boundary instead.
    """
    block = _render_block(citation_id, meta, page, content)
    if token_counter(block) <= limit:
        return content, False
    return _truncate_content(
        content=content,
        citation_id=citation_id,
        meta=meta,
        page=page,
        limit=limit,
        token_counter=token_counter,
    )


def _truncate_content(
    *,
    content: str,
    citation_id: str,
    meta: DocumentMeta,
    page: int | None,
    limit: int,
    token_counter: Callable[[str], int],
) -> tuple[str | None, bool]:
    sentences = [sentence for sentence in _SENTENCE_RE.split(content) if sentence.strip()]
    kept = ""
    for sentence in sentences:
        candidate = f"{kept} {sentence}".strip()
        if token_counter(_render_block(citation_id, meta, page, candidate)) <= limit:
            kept = candidate
        else:
            break
    if kept:
        return kept, True

    # A single sentence still does not fit: trim on a character boundary so the
    # region is never empty and never over budget.
    low, high = 0, len(content)
    best = ""
    while low <= high:
        middle = (low + high) // 2
        candidate = content[:middle].rstrip()
        if candidate and token_counter(_render_block(citation_id, meta, page, candidate)) <= limit:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    if not best:
        return None, False
    return best, True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _render_block(citation_id: str, meta: DocumentMeta, page: int | None, content: str) -> str:
    page_attribute = f' page="{page}"' if page is not None else ""
    return (
        f'{EVIDENCE_OPEN_TAG} id="{citation_id}" source="{_attribute(meta.filename)}"'
        f'{page_attribute} source_type="{meta.source_type.value}">\n'
        f"{content}\n"
        f"{EVIDENCE_CLOSE_TAG}"
    )


def _neutralise(content: str) -> str:
    """Remove reserved markers so document text cannot close the region early.

    Delegates to :func:`coursellm.security.sanitize.neutralise_markers` so that
    assembly and ingestion share one implementation and cannot drift on what a
    reserved marker is.
    """
    return neutralise_markers(content)


def _attribute(value: str) -> str:
    """Escape a value placed inside a double-quoted tag attribute."""
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


__all__ = [
    "EVIDENCE_CLOSE_TAG",
    "EVIDENCE_OPEN_TAG",
    "AssembledContext",
    "ChunkPosition",
    "Citation",
    "ContextPassage",
    "DocumentMeta",
    "assemble",
]
