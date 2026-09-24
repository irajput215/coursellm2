"""The knowledge-graph vocabulary and the extraction contract.

``EdgeRelation`` is the one place the six relation names are declared. It is a
:class:`~enum.StrEnum` so the stored value is the readable name and the SQLAlchemy
column is a plain ``VARCHAR`` plus a ``CHECK`` constraint rather than a native
PostgreSQL enum: adding a relation later is then an ordinary, reviewable
migration instead of an ``ALTER TYPE`` that cannot always run in a transaction.

The three Pydantic models below are the *entire* structured-output contract for
extraction. ``extra="forbid"`` is deliberate — a model that invents a field is
returning a malformed object and the extraction should fail loudly rather than
silently dropping the unknown key. Two invariants are enforced at this layer
because no later gate can be relied on to see them:

* an inferred ``requires`` edge is refused outright (§5.2) — it is the single
  most damaging extraction error, because it can reorder a roadmap or introduce a
  cycle, and it must rest on an explicit textual cue; and
* a self-relation is refused before any slug normalisation is attempted.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

#: The shape every slug and normalised alias must satisfy. ASCII, lower-case,
#: hyphen-separated: it is a key, not a display string, so it is deliberately
#: conservative (``docs/architecture/knowledge-graph.md`` §3.1).
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


class EdgeRelation(StrEnum):
    """How a directed edge is read. Direction is declared once, used everywhere.

    ``requires`` is the only relation that participates in prerequisite closure.
    ``related_to`` is deliberately non-transitive and is never traversed for a
    closure: chaining associations produces plausible nonsense (§3.3).
    """

    REQUIRES = "requires"
    CONTAINS = "contains"
    RELATED_TO = "related_to"
    PART_OF = "part_of"
    ASSESSES = "assesses"
    TAUGHT_BY = "taught_by"


#: The name the architecture document uses for the same enum. Exported so code
#: written against either spelling resolves to the single definition above.
ConceptRelation = EdgeRelation

#: Relations that may be traversed transitively for a closure. ``related_to``,
#: ``assesses`` and ``taught_by`` are excluded by construction.
CLOSURE_RELATIONS: frozenset[EdgeRelation] = frozenset(
    {EdgeRelation.REQUIRES, EdgeRelation.CONTAINS, EdgeRelation.PART_OF}
)

_valid_relation_values: frozenset[str] = frozenset(member.value for member in EdgeRelation)


def slugify(name: str) -> str:
    """Return the deterministic slug for a surface form.

    NFKC, case-folded, punctuation collapsed to a single hyphen, trimmed. This
    is what makes deduplication mechanical: two spellings of the same concept
    produce the same key without a second model call.
    """
    normalised = unicodedata.normalize("NFKC", name).casefold()
    return _NON_SLUG_RE.sub("-", normalised).strip("-")


def normalise_alias(alias: str) -> str:
    """Alias resolution uses the same normalisation as slugs, on purpose."""
    return slugify(alias)


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.fullmatch(slug))


class ExtractedConcept(BaseModel):
    """One concept asserted by an extraction, with the sentence that supports it."""

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=2, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    description: str | None = Field(default=None, max_length=600)
    difficulty: int = Field(ge=1, le=5, default=3)
    source_chunk_id: UUID
    source_quote: str = Field(min_length=10, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("name")
    @classmethod
    def _name_no_control_chars(cls, value: str) -> str:
        if any(ord(ch) < 32 for ch in value):
            msg = "control characters are not permitted"
            raise ValueError(msg)
        return value

    @field_validator("aliases")
    @classmethod
    def _aliases_no_control_chars(cls, value: list[str]) -> list[str]:
        for alias in value:
            if any(ord(ch) < 32 for ch in alias):
                msg = "control characters are not permitted"
                raise ValueError(msg)
        return value


class ExtractedRelation(BaseModel):
    """One directed edge asserted by an extraction.

    ``cue`` is the model's own report of whether the text states the relation
    explicitly ("X requires Y") or merely implies it. It is a *signal*, not a
    verdict: :mod:`coursellm.graph.confidence` weights it, and the schema refuses
    the one combination that is never acceptable.
    """

    model_config = {"extra": "forbid"}

    source_name: str = Field(min_length=2, max_length=200)
    target_name: str = Field(min_length=2, max_length=200)
    relation: EdgeRelation
    rationale: str = Field(max_length=400)
    source_quote: str = Field(min_length=10, max_length=1000)
    cue: Literal["explicit", "inferred"] = "inferred"
    confidence: float = Field(ge=0.0, le=1.0)
    chunk_id: UUID

    @model_validator(mode="after")
    def _requires_is_never_inferred(self) -> ExtractedRelation:
        if self.relation is EdgeRelation.REQUIRES and self.cue == "inferred":
            msg = "requires edges must cite an explicit textual cue"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _no_self_relation(self) -> ExtractedRelation:
        if self.source_name.strip().casefold() == self.target_name.strip().casefold():
            msg = "self-relations are not permitted"
            raise ValueError(msg)
        return self


class ExtractionResult(BaseModel):
    """The single structured object one extraction call returns for one chunk."""

    model_config = {"extra": "forbid"}

    concepts: list[ExtractedConcept] = Field(default_factory=list, max_length=40)
    relations: list[ExtractedRelation] = Field(default_factory=list, max_length=80)


def raw_relation_value(relation: Any) -> str | None:
    """Return the string value of a relation that may have bypassed validation.

    The validation gates are tested directly, so they must tolerate an object
    built with ``model_construct`` (which skips validators). This helper is what
    lets the relation-enum gate reject a value the schema would already have
    caught in the normal path.
    """
    if isinstance(relation, EdgeRelation):
        return relation.value
    if isinstance(relation, str):
        return relation
    return None


__all__ = [
    "CLOSURE_RELATIONS",
    "SLUG_RE",
    "ConceptRelation",
    "EdgeRelation",
    "ExtractedConcept",
    "ExtractedRelation",
    "ExtractionResult",
    "is_valid_slug",
    "normalise_alias",
    "raw_relation_value",
    "slugify",
]
