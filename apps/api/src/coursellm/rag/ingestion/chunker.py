"""Token-aware, paragraph-greedy recursive chunking.

The strategy is a small cascade, applied to one page at a time:

1. Split the page on blank lines into paragraphs.
2. Pack whole paragraphs greedily until the next one would exceed
   ``max_tokens``.
3. If a single paragraph is too large, split it on sentence boundaries and pack
   sentences.
4. If a single sentence is still too large, hard-split it on word ("token")
   boundaries.

Each level only runs when the level above it cannot fit a unit, which keeps the
common case cheap and the pathological case bounded.

## Chunks never span a page boundary

This is a citation decision, not a performance one. ``Chunk.page`` is what a
generated answer cites back to the student, so a chunk that contains the end of
page 4 and the start of page 5 has no single honest page number. Page-local
chunking keeps every citation exact at the cost of slightly worse packing at the
seam, which is the right trade for a study tool.

## Overlap

Overlap is computed on analysed terms: the final ``overlap_tokens`` terms of a
finished chunk are prepended to the next chunk. It is therefore a true suffix of
the previous chunk and a true prefix of the next in *term* space (the overlap
text is rendered from the analyser's term sequence, so its original casing and
some punctuation are normalised away — correctness of retrieval depends on terms
matching, not on the overlap preserving typography).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from coursellm.core.config import Settings
from coursellm.core.errors import ValidationError
from coursellm.rag.analyzers import tokenize
from coursellm.rag.ingestion.parsers import ParsedPage

# A blank line (optionally containing whitespace) separates paragraphs.
_PARAGRAPH_RE = re.compile(r"\n\s*\n")
# Sentence-final punctuation followed by whitespace. Deliberately simple: the
# analyser, not the splitter, is responsible for term quality, and an
# over-eager sentence split only changes where a chunk boundary may fall.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before it is persisted (no id, no document yet)."""

    content: str
    page: int
    chunk_index: int
    token_count: int
    starts_mid_sentence: bool


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunking parameters, validated at construction.

    The ``overlap_tokens < max_tokens`` guard is the reason this is a dataclass
    with a ``__post_init__`` rather than three loose integers. A configuration
    where overlap meets or exceeds the size yields a non-positive advance, so the
    previous implementation's split loop either emitted overlapping-identical
    chunks forever or produced an empty slice; both are silent corruption of the
    index rather than a startup error.
    """

    max_tokens: int
    overlap_tokens: int
    min_tokens: int

    def __post_init__(self) -> None:
        if self.max_tokens <= 0:
            raise ValidationError("chunk max_tokens must be positive.")
        if self.overlap_tokens < 0:
            raise ValidationError("chunk overlap_tokens must not be negative.")
        if self.overlap_tokens >= self.max_tokens:
            raise ValidationError(
                "chunk overlap_tokens must be smaller than max_tokens "
                f"(got overlap={self.overlap_tokens}, max={self.max_tokens}); "
                "an overlap that reaches the chunk size never advances the window."
            )
        if self.min_tokens <= 0:
            raise ValidationError("chunk min_tokens must be positive.")
        if self.min_tokens > self.max_tokens:
            raise ValidationError(
                "chunk min_tokens must not exceed max_tokens "
                f"(got min={self.min_tokens}, max={self.max_tokens})."
            )

    @classmethod
    def from_settings(cls, settings: Settings) -> ChunkingConfig:
        return cls(
            max_tokens=settings.chunk_size_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            min_tokens=settings.min_chunk_tokens,
        )

    @property
    def version(self) -> str:
        return chunking_config_version(self)


def chunking_config_version(config: ChunkingConfig) -> str:
    """Stable identifier for a chunking configuration.

    Stored on :class:`~coursellm.db.models.content.Document` so that changing the
    chunker is a reviewable, selectable migration: the documents produced by the
    old settings are exactly those whose ``chunking_config_version`` differs.
    """
    payload = f"{config.max_tokens}:{config.overlap_tokens}:{config.min_tokens}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class _Unit:
    """A packable piece of text and the separator that precedes it.

    ``prefix`` is ``"\\n\\n"`` when the unit opens a new paragraph and ``" "``
    when it continues one (or continues a hard-split sentence).
    """

    text: str
    token_count: int
    prefix: str
    starts_mid_sentence: bool


def _token_count(text: str) -> int:
    return len(tokenize(text))


def chunk_pages(pages: Sequence[ParsedPage], config: ChunkingConfig) -> list[ChunkDraft]:
    """Chunk every page independently and return document-contiguous drafts."""
    drafts: list[ChunkDraft] = []
    for page in pages:
        units = _page_units(page.text, config)
        for content, starts_mid in _pack(units, config):
            drafts.append(
                ChunkDraft(
                    content=content,
                    page=page.page,
                    chunk_index=len(drafts),
                    token_count=_token_count(content),
                    starts_mid_sentence=starts_mid,
                )
            )

    if not drafts:
        return []

    # ``min_tokens`` exists to drop fragments such as a trailing orphan line. It
    # must never be able to empty the document: a READY document with zero
    # chunks is retrievable by nothing, which is indistinguishable from a failed
    # ingest except that it reports success.
    total_tokens = sum(draft.token_count for draft in drafts)
    if total_tokens < config.min_tokens:
        kept = [max(drafts, key=lambda draft: draft.token_count)]
    else:
        kept = [draft for draft in drafts if draft.token_count >= config.min_tokens]
        if not kept:
            kept = [max(drafts, key=lambda draft: draft.token_count)]

    return [replace(draft, chunk_index=index) for index, draft in enumerate(kept)]


def _page_units(text: str, config: ChunkingConfig) -> list[_Unit]:
    units: list[_Unit] = []
    for paragraph in _PARAGRAPH_RE.split(text):
        stripped = paragraph.strip()
        if not stripped:
            continue
        prefix = "\n\n" if units else ""
        units.extend(_expand(stripped, prefix, starts_mid=False, config=config))
    return units


def _expand(text: str, prefix: str, starts_mid: bool, config: ChunkingConfig) -> list[_Unit]:
    """Recursively reduce ``text`` until every returned unit fits ``max_tokens``.

    A single word longer than ``max_tokens`` is returned as-is rather than
    dropped; it is pathological (no natural language produces it) and looping on
    it would hang ingestion.
    """
    tokens = _token_count(text)
    if tokens == 0:
        # A paragraph of pure punctuation contributes no retrievable terms; it
        # must not become a zero-token chunk, which would violate the
        # ``chunks.token_count > 0`` constraint.
        return []
    if tokens <= config.max_tokens:
        return [_Unit(text=text, token_count=tokens, prefix=prefix, starts_mid_sentence=starts_mid)]

    sentences = [sentence for sentence in _SENTENCE_RE.split(text) if sentence.strip()]
    if len(sentences) > 1:
        units: list[_Unit] = []
        for index, sentence in enumerate(sentences):
            units.extend(
                _expand(
                    sentence,
                    prefix if index == 0 else " ",
                    starts_mid if index == 0 else False,
                    config,
                )
            )
        return units

    units = []
    for index, word in enumerate(text.split()):
        units.append(
            _Unit(
                text=word,
                token_count=_token_count(word),
                prefix=prefix if index == 0 else " ",
                starts_mid_sentence=starts_mid if index == 0 else True,
            )
        )
    return units


def _render(units: Sequence[_Unit]) -> str:
    return "".join(
        unit.text if index == 0 else unit.prefix + unit.text for index, unit in enumerate(units)
    )


def _overlap_unit(content: str, overlap_tokens: int) -> _Unit | None:
    """The suffix of ``content`` to carry into the next chunk, or ``None``."""
    tokens = tokenize(content)
    if not tokens or overlap_tokens <= 0:
        return None
    tail = tokens[-overlap_tokens:]
    return _Unit(text=" ".join(tail), token_count=len(tail), prefix="", starts_mid_sentence=False)


def _take_head(unit: _Unit, budget: int) -> tuple[_Unit, _Unit] | None:
    """Split ``unit`` into a head that fits ``budget`` terms and a tail.

    Returns ``None`` when the unit is already a single word, so the caller can
    stop rather than spin.
    """
    words = unit.text.split()
    if len(words) <= 1:
        return None

    head_words: list[str] = []
    used = 0
    index = 0
    for position, word in enumerate(words):
        word_tokens = _token_count(word)
        # Always take the first word, even if it alone exceeds the budget: the
        # alternative is an empty head and an infinite loop.
        if head_words and used + word_tokens > budget:
            index = position
            break
        head_words.append(word)
        used += word_tokens
        index = position + 1

    if not head_words or index >= len(words):
        return None

    tail_text = " ".join(words[index:])
    return (
        _Unit(
            text=" ".join(head_words),
            token_count=used,
            prefix=unit.prefix,
            starts_mid_sentence=unit.starts_mid_sentence,
        ),
        _Unit(
            text=tail_text,
            token_count=_token_count(tail_text),
            prefix=" ",
            starts_mid_sentence=True,
        ),
    )


def _pack(units: Sequence[_Unit], config: ChunkingConfig) -> list[tuple[str, bool]]:
    """Greedily pack units into chunks, carrying overlap between them."""
    chunks: list[tuple[str, bool]] = []
    current: list[_Unit] = []
    current_tokens = 0
    current_starts_mid: bool | None = None

    def flush() -> None:
        nonlocal current, current_tokens, current_starts_mid
        if not current:
            return
        chunks.append((_render(current), bool(current_starts_mid)))
        current = []
        current_tokens = 0
        current_starts_mid = None

    def reopen(from_content: str) -> None:
        nonlocal current, current_tokens, current_starts_mid
        overlap = _overlap_unit(from_content, config.overlap_tokens)
        current = [overlap] if overlap is not None else []
        current_tokens = overlap.token_count if overlap is not None else 0
        current_starts_mid = None

    for unit in units:
        if current and current_tokens + unit.token_count > config.max_tokens:
            content = _render(current)
            flush()
            reopen(content)

        # The overlap carried in can leave less room than the incoming unit
        # needs even after a flush. Split it rather than exceed the budget.
        while current_tokens + unit.token_count > config.max_tokens:
            budget = config.max_tokens - current_tokens
            split = _take_head(unit, budget)
            if split is None:
                break
            head, unit = split
            current.append(head)
            if current_starts_mid is None:
                current_starts_mid = head.starts_mid_sentence
            current_tokens += head.token_count
            content = _render(current)
            flush()
            reopen(content)

        current.append(unit)
        if current_starts_mid is None:
            current_starts_mid = unit.starts_mid_sentence
        current_tokens += unit.token_count

    flush()
    return chunks
