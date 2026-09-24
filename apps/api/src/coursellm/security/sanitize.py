"""Neutralisation and redaction helpers shared by ingestion and assembly.

There is exactly one implementation of reserved-marker stripping in the system,
and it lives here. ``docs/architecture/security.md`` section 3.1 explains why the
fence is built at assembly: assembly is the only point where the
``<untrusted_evidence>`` region is actually constructed, so it is the only point
that *has* to be correct. Ingestion calls the same function so a future consumer
that renders stored passages directly is protected too, and so the two call sites
cannot drift on what counts as a marker.

The helpers are intentionally total: every input returns a string, nothing
raises, and the transformations are idempotent.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from coursellm.security.injection import InjectionVerdict

#: The region's own delimiters. A passage that contains one could close the
#: fence early and "escape" into instruction position.
_RESERVED_TAG_RE = re.compile(r"</?untrusted_evidence[^>]*>", re.IGNORECASE)
_RESERVED_PREFIX_RE = re.compile(r"</?untrusted_evidence", re.IGNORECASE)

#: Legacy chat-template control tokens the generation models were trained on.
#: They are a role/prompt boundary in every model that understands them.
_LEGACY_TOKENS_RE = re.compile(
    r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"
    r"|<\|(?:start_header_id|end_header_id)\|>"
    r"|\[/?INST\]"
    r"|<<SYS>>|<</SYS>>",
    re.IGNORECASE,
)

#: Markdown-style role headers that read as a new turn inside a passage.
_ROLE_HEADER_RE = re.compile(
    r"(?m)^[ \t]*#{1,6}[ \t]*(?:system|assistant|developer|human|user)[ \t]*:",
    re.IGNORECASE,
)

#: Zero-width and bidirectional control characters. They are invisible in a
#: rendered document but present in extracted text, which is exactly how a
#: hidden instruction layer is built.
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\U000e0000-\U000e007f]")
#: C0/C1 control characters except tab, newline and carriage return.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

_WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def neutralise_markers(text: str) -> str:
    """Remove reserved markers, invisible characters and control codes.

    Order matters only for readability: the reserved tag is removed before its
    bare prefix so a partially-formed tag cannot leave ``untrusted_evidence``
    behind, and role headers are removed after the tag patterns.
    """
    if not text:
        return text
    result = _RESERVED_TAG_RE.sub("", text)
    result = _RESERVED_PREFIX_RE.sub("", result)
    result = _LEGACY_TOKENS_RE.sub("", result)
    result = _ROLE_HEADER_RE.sub("", result)
    result = _INVISIBLE_RE.sub("", result)
    result = _CONTROL_RE.sub("", result)
    return unicodedata.normalize("NFKC", result)


def strip_invisibles(text: str) -> str:
    """Remove zero-width, bidirectional and Unicode-tag characters only."""
    if not text:
        return text
    return _CONTROL_RE.sub("", _INVISIBLE_RE.sub("", text))


def sanitize_query(text: str, verdict: InjectionVerdict, *, max_chars: int | None = None) -> str:
    """Remove the injected instruction spans from ``text``, keeping the question.

    Refusing to answer a legitimate question that merely contains the word
    "ignore" would be a worse failure than the injection it guards against, so a
    ``sanitize`` verdict edits rather than discards: the recorded match spans are
    cut out, whitespace is collapsed, and the remainder — the student's actual
    question — is preserved. Encoded-payload spans are cut as whole blobs; the
    decode-derived child spans point at the same blob, so they collapse with it.

    ``max_chars`` defaults to no truncation; callers pass
    ``settings.max_query_chars``. A result that is empty after the edit falls
    back to :func:`neutralise_markers` on the original text, because an empty
    prompt is never a better answer than a sanitised one.
    """
    if not text:
        return text
    spans = _merge_spans(match.span for match in verdict.matches if 0 < match.span[1] <= len(text))
    kept = _remove_spans(text, spans)
    kept = neutralise_markers(kept)
    kept = _WHITESPACE_RE.sub(" ", kept).strip()
    # Cutting an instruction out of the middle of a sentence can leave the
    # punctuation that terminated it in front of the student's actual question.
    kept = kept.lstrip(".,;:!?)]} \t")
    kept = _BLANK_LINES_RE.sub("\n\n", kept)
    if not kept:
        fallback = _WHITESPACE_RE.sub(" ", neutralise_markers(text)).strip()
        kept = fallback
    if max_chars is not None and len(kept) > max_chars:
        kept = kept[:max_chars].rstrip()
    return kept


def scrub_for_storage(text: str) -> str:
    """Neutralise anything persisted that might later be rendered.

    Used by ingestion for chunk content and by any caller that stores
    model-influenced text. Deliberately does not truncate: content length is the
    ingestion chunker's concern.
    """
    return neutralise_markers(text)


def _merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((start, end) for start, end in spans if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    return " ".join(piece for piece in pieces if piece.strip())


__all__ = [
    "neutralise_markers",
    "sanitize_query",
    "scrub_for_storage",
    "strip_invisibles",
]
