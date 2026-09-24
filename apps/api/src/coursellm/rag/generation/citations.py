"""Citation extraction, verification and hallucination stripping.

A generated answer earns the right to cite only the passages that were actually
placed in its context. Anything else is a **citation hallucination**: a claim of
support that does not exist, which is worse than no citation at all because it
survives a reader's spot check. This module is the small, pure, testable core
that enforces that.

Three marker forms are recognised, because models produce all three:
``[S1]``, ``[S1][S2]`` and ``[S1, S2]``.
"""

from __future__ import annotations

import re
from collections.abc import Set
from dataclasses import dataclass, field

_GROUP_RE = re.compile(r"\[([^\]]*)\]")
_ID_RE = re.compile(r"\AS\d+\Z")
_SEPARATOR_RE = re.compile(r"[,\s]+")

#: The phrase the refusal template is built around. Kept here so that refusal
#: detection and the template cannot drift apart.
REFUSAL_MARKER = "I don't have enough information"

# Additional phrasings a model may use when it declines to answer. Detection is
# substring-based and case-insensitive; the rule is documented with the metrics.
_REFUSAL_PHRASES: tuple[str, ...] = (
    REFUSAL_MARKER.lower(),
    "not enough information",
    "not enough evidence",
    "insufficient evidence",
    "cannot answer",
    "can't answer",
    "cannot be answered",
    "do not have enough context",
)


@dataclass(frozen=True, slots=True)
class CitationReport:
    """How an answer's citations relate to the citations it was offered.

    ``valid`` are cited ids that resolve; ``hallucinated`` are cited ids that do
    not; ``unused`` are offered ids the answer never cited.
    """

    cited: list[str] = field(default_factory=list)
    valid: list[str] = field(default_factory=list)
    hallucinated: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    citation_precision: float = 0.0
    citation_recall: float = 0.0


def extract_citation_ids(text: str) -> list[str]:
    """Return cited ids in first-appearance order, de-duplicated."""
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _GROUP_RE.finditer(text):
        for citation_id in _ids_in(match.group(1)):
            if citation_id not in seen:
                seen.add(citation_id)
                ordered.append(citation_id)
    return ordered


def verify_citations(
    text: str,
    available: Set[str],
    valid_ids: Set[str] | None = None,
) -> CitationReport:
    """Compare the citations in ``text`` against the citations offered.

    ``available`` is every citation id that was placed in the context.
    ``valid_ids`` is the subset that resolves to a citation record; it defaults
    to ``available``. ``valid`` is the cited ids that resolve, ``hallucinated``
    the cited ids that are not in ``available``, and ``unused`` the offered ids
    that were never cited.

    **The empty-citation rule.** When nothing is cited, precision is ``1.0`` if
    the answer is a refusal and ``0.0`` otherwise. A refusal makes no citation
    claim, so it is not a precision failure; an ordinary answer that cites
    nothing is unsupported and is. Recall is ``len(valid) / len(available)``, or
    ``0.0`` when nothing was available to recall.
    """
    available_set = set(available)
    resolvable = set(valid_ids) if valid_ids is not None else set(available_set)

    cited = extract_citation_ids(text)
    cited_set = set(cited)
    valid = [
        citation_id
        for citation_id in cited
        if citation_id in available_set and citation_id in resolvable
    ]
    hallucinated = [citation_id for citation_id in cited if citation_id not in available_set]
    unused = sorted(
        (citation_id for citation_id in available_set if citation_id not in cited_set),
        key=_sort_key,
    )

    if cited:
        precision = len(valid) / len(cited)
    elif is_refusal(text):
        precision = 1.0
    else:
        precision = 0.0
    recall = len(valid) / len(available_set) if available_set else 0.0

    return CitationReport(
        cited=cited,
        valid=valid,
        hallucinated=hallucinated,
        unused=unused,
        citation_precision=precision,
        citation_recall=recall,
    )


def strip_hallucinated(text: str, available: Set[str]) -> tuple[str, list[str]]:
    """Remove the markers of ids that do not exist, leaving the prose intact.

    A bracket group that contains no citation id at all is ordinary prose and is
    left untouched. A group that mixes real and invented ids keeps the real ones
    and drops the rest. The returned list names the ids that were removed, in
    first-appearance order and de-duplicated.
    """
    available_set = set(available)
    removed: list[str] = []
    removed_seen: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        ids = _ids_in(inner)
        if not ids:
            return match.group(0)
        kept: list[str] = []
        for citation_id in ids:
            if citation_id in available_set:
                if citation_id not in kept:
                    kept.append(citation_id)
                continue
            if citation_id not in removed_seen:
                removed_seen.add(citation_id)
                removed.append(citation_id)
        if not kept:
            return ""
        return "[" + ", ".join(kept) + "]"

    return _GROUP_RE.sub(_replace, text), removed


def is_refusal(text: str) -> bool:
    """Whether ``text`` declines to answer rather than answering.

    Substring-based and case-insensitive: a model that refuses in its own words
    still counts as a refusal, which is what the empty-citation precision rule
    needs.
    """
    lowered = text.lower()
    return any(phrase in lowered for phrase in _REFUSAL_PHRASES)


def _ids_in(inner: str) -> list[str]:
    return [token for token in _SEPARATOR_RE.split(inner.strip()) if _ID_RE.match(token)]


def _sort_key(citation_id: str) -> int:
    digits = citation_id[1:]
    return int(digits) if digits.isdigit() else 0


__all__ = [
    "REFUSAL_MARKER",
    "CitationReport",
    "extract_citation_ids",
    "is_refusal",
    "strip_hallucinated",
    "verify_citations",
]
