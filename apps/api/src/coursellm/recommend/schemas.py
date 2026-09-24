"""Typed vocabulary and response models for recommendations.

Two things live here and nothing else:

* **The controlled vocabularies.** ``ResourceType``, ``SourceTrust`` and
  ``Difficulty`` are the values the database stores, the ranking reads and the
  API serialises. They are defined here rather than in the ORM module so that the
  pure ranking and explanation functions can import them without importing
  SQLAlchemy; :mod:`coursellm.db.models.resource` imports *them*, not the other
  way round.
* **The explanation shape.** :class:`RecommendationExplanation` is the typed
  "why this resource?" answer. It is a Pydantic model because it crosses the API
  boundary, but it is produced by deterministic composition in
  :mod:`coursellm.recommend.explanation`, never by a model call.

Difficulty is an :class:`~enum.IntEnum` (1-5) rather than a PostgreSQL enum. It is
stored as an integer with a ``BETWEEN 1 AND 5`` check constraint: an integer
compares and interpolates (the ranking fits a resource's difficulty against a
computed target), and a small ordered scale does not benefit from being an enum
type in the database. The named members exist so application code can say
``Difficulty.INTERMEDIATE`` instead of a bare ``3``.
"""

from __future__ import annotations

import uuid
from enum import IntEnum, StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResourceType(StrEnum):
    """What kind of thing a catalogue entry is."""

    COURSE = "course"
    MOOC = "mooc"
    BOOK = "book"
    DOCUMENTATION = "documentation"
    PAPER = "paper"
    TUTORIAL = "tutorial"
    VIDEO = "video"
    STANDARD = "standard"


class SourceTrust(StrEnum):
    """How much authority a source carries (``security.md`` section 8.2).

    The ordering is deliberate and monotone: a ranking term reads these levels,
    and the seed-integrity test asserts that a level is only used for a host that
    can legitimately carry it.
    """

    OFFICIAL = "official"
    ACADEMIC = "academic"
    COMMUNITY = "community"
    SECONDARY = "secondary"


class Difficulty(IntEnum):
    """A 1-5 difficulty band, used for concepts and resources alike."""

    FOUNDATIONAL = 1
    BEGINNER = 2
    INTERMEDIATE = 3
    ADVANCED = 4
    EXPERT = 5


#: The inclusive bounds of the difficulty scale, shared by the model's check
#: constraint and the ranking's fit function.
MIN_DIFFICULTY = int(Difficulty.FOUNDATIONAL)
MAX_DIFFICULTY = int(Difficulty.EXPERT)


class GapSummary(BaseModel):
    """One knowledge gap the recommendation set was built to close.

    ``mastery`` is the projected value (0.0 when there is no evidence) and
    ``never_assessed`` distinguishes "measured weak" from "no evidence at all",
    because the two call for different wording even though both are gaps.
    """

    model_config = ConfigDict(extra="forbid")

    concept_id: uuid.UUID
    slug: str
    name: str
    difficulty: int = Field(ge=MIN_DIFFICULTY, le=MAX_DIFFICULTY)
    mastery: float = Field(ge=0.0, le=1.0)
    never_assessed: bool


class RecommendationExplanation(BaseModel):
    """The deterministic answer to "why this resource?".

    Every field is derived from the typed score decomposition: ``covered_gap_*``
    can only name a gap the resource genuinely covers (it is the intersection of
    the resource's concept slugs with the gaps, not a paraphrase), and
    ``contributions`` is the exact vector the ranking summed. A model may rephrase
    ``summary`` elsewhere, but it may never add a claim that is not already in
    these fields.
    """

    model_config = ConfigDict(extra="forbid")

    resource_id: uuid.UUID
    summary: str
    primary_gap_slug: str | None
    primary_gap_name: str | None
    covered_gap_slugs: list[str]
    covered_gap_names: list[str]
    next_gap_slug: str | None
    next_gap_name: str | None
    next_step: str
    coverage: float = Field(ge=0.0, le=1.0)
    contributions: dict[str, float]
    personalised: bool


__all__ = [
    "MAX_DIFFICULTY",
    "MIN_DIFFICULTY",
    "Difficulty",
    "GapSummary",
    "RecommendationExplanation",
    "ResourceType",
    "SourceTrust",
]
