"""Tenant-scoped access to the concept graph: the five documented CTE queries.

Every statement here repeats ``tenant_id`` in every branch, including the branch
that Row-Level Security would already filter. That is not redundancy for its own
sake: the predicate is what lets PostgreSQL use the composite indexes
(``idx_concept_edges_out``/``_in``), and RLS is the backstop rather than the only
control (``docs/architecture/knowledge-graph.md`` §3.6).

Every recursive traversal carries **both** a depth cap and a ``path`` cycle
guard (``WHERE NOT c.id = ANY(path)``). Neither substitutes for the other: the
depth cap bounds the work regardless of graph shape, and the path guard is what
makes the query *correct and terminating* on a cyclic input that a bypassed write
path allowed into the table (§3.7, §4.1). ``UNION ALL`` is used deliberately —
``UNION`` would deduplicate rows, silently changing which paths are explored,
and would still permit exponential re-expansion.

The :class:`~coursellm.graph.repository.ConceptGraphRepository` is the only
adapter over the graph tables. If the store were ever moved to a graph database
(§2.3), this module is the single thing that changes.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import RowMapping, case, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from coursellm.core.errors import CourseLLMError, NotFoundError, ValidationError
from coursellm.db.models.graph import Concept, ConceptAlias, ConceptEdge
from coursellm.graph.confidence import GRAPH_MIN_TRAVERSABLE_CONFIDENCE
from coursellm.graph.schemas import (
    CLOSURE_RELATIONS,
    EdgeRelation,
    is_valid_slug,
    normalise_alias,
    slugify,
)
from coursellm.repositories.base import TenantRepository

#: Default traversal bound. Configuration owns the deployed value
#: (``GRAPH_MAX_DEPTH``); the literal keeps the repository usable without one.
GRAPH_DEFAULT_MAX_DEPTH = 3
#: Default confidence floor for traversal.
GRAPH_DEFAULT_MIN_CONFIDENCE = GRAPH_MIN_TRAVERSABLE_CONFIDENCE
#: Bound on the cycle-detection path length, independently of graph shape.
GRAPH_DEFAULT_MAX_PATH_LEN = 20

_CLOSURE_RELATION_VALUES: frozenset[str] = frozenset(r.value for r in CLOSURE_RELATIONS)


class GraphCycleError(CourseLLMError):
    """Raised when a roadmap cannot be produced because a cycle exists.

    A topological sort that cannot complete identifies the remaining nodes; the
    repository reports them in :class:`RoadmapPlan` so the review UI can show
    them. This error exists for callers that asked for a strict plan instead.
    """

    status_code = 409
    error_code = "graph_cycle"


@dataclass(frozen=True, slots=True)
class ClosureNode:
    """One prerequisite reached by :meth:`prerequisite_closure`."""

    concept_id: uuid.UUID
    name: str
    slug: str
    difficulty: int
    depth: int
    path: tuple[uuid.UUID, ...]
    weight: float
    confidence: float
    verified: bool
    provenance_document_id: uuid.UUID | None
    provenance_chunk_id: uuid.UUID | None
    provenance_page: int | None


@dataclass(frozen=True, slots=True)
class DirectPrerequisite:
    """One direct ``requires`` edge's target, with its evidence."""

    concept_id: uuid.UUID
    name: str
    slug: str
    difficulty: int
    weight: float
    confidence: float
    verified: bool
    cue: str
    provenance_document_id: uuid.UUID | None
    provenance_chunk_id: uuid.UUID | None
    provenance_page: int | None
    source_document_title: str | None


@dataclass(frozen=True, slots=True)
class RelatedConcept:
    """A direct association or a sibling under a shared ``part_of`` parent."""

    concept_id: uuid.UUID
    name: str
    slug: str
    difficulty: int
    relation: str
    kind: str
    weight: float
    confidence: float
    verified: bool
    provenance_chunk_id: uuid.UUID | None
    provenance_page: int | None


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    """A prerequisite of the target for which the student has no mastery evidence."""

    concept_id: uuid.UUID
    name: str
    slug: str
    difficulty: int
    depth: int
    path: tuple[uuid.UUID, ...]
    mastery: float
    never_assessed: bool


@dataclass(frozen=True, slots=True)
class CycleEdge:
    """One cycle found by :meth:`detect_cycles`."""

    source_concept_id: uuid.UUID
    target_concept_id: uuid.UUID
    path: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class ClosureEdge:
    """An edge whose two endpoints are inside a closure."""

    source_concept_id: uuid.UUID
    target_concept_id: uuid.UUID
    verified: bool
    weight: float


@dataclass(frozen=True, slots=True)
class RoadmapStep:
    """One ordered step of a roadmap."""

    concept_id: uuid.UUID
    name: str
    slug: str
    difficulty: int
    depth: int


@dataclass(frozen=True, slots=True)
class RoadmapPlan:
    """A deterministic topological order, plus any nodes a cycle stranded."""

    steps: tuple[RoadmapStep, ...]
    unmet_cycle: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class GraphEntityRow:
    """One typed entity returned by :meth:`search_entities`."""

    concept_id: uuid.UUID
    name: str
    slug: str
    relation: str
    depth: int
    direction: str
    weight: float
    confidence: float
    verified: bool
    path: tuple[uuid.UUID, ...]
    provenance: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GraphSearchOutcome:
    """A closure or expansion, with how it degraded."""

    entities: tuple[GraphEntityRow, ...]
    degraded: tuple[str, ...]
    depth_used: int


def _as_uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _as_optional_uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else _as_uuid(value)


def _as_path(value: Any) -> tuple[uuid.UUID, ...]:
    if value is None:
        return ()
    return tuple(_as_uuid(item) for item in value)


def _as_str(value: Any) -> str:
    return str(value)


def _as_optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_int(value: Any) -> int:
    return int(value)


def _as_float(value: Any) -> float:
    return float(value)


def _as_bool(value: Any) -> bool:
    return bool(value)


def _validate_relation(relation: str) -> str:
    if relation not in _CLOSURE_RELATION_VALUES:
        msg = (
            f"{relation!r} is not a transitive closure relation; expected one of "
            f"{sorted(_CLOSURE_RELATION_VALUES)}."
        )
        raise ValidationError(msg)
    return relation


def _mastered_ids(mastered: Mapping[str | uuid.UUID, float], threshold: float) -> set[uuid.UUID]:
    """Keys that are mastered above ``threshold``, tolerating string or UUID keys."""
    result: set[uuid.UUID] = set()
    for key, value in mastered.items():
        try:
            concept_id = key if isinstance(key, uuid.UUID) else uuid.UUID(str(key))
        except ValueError:
            continue
        if float(value) >= threshold:
            result.add(concept_id)
    return result


_PREREQUISITE_CLOSURE_SQL = """
    WITH RECURSIVE prereqs AS (
        SELECT
            e.target_concept_id          AS concept_id,
            c.name,
            c.slug,
            c.difficulty,
            1                            AS depth,
            ARRAY[e.source_concept_id, e.target_concept_id] AS path,
            e.weight,
            e.confidence,
            e.verified,
            e.provenance_document_id,
            e.provenance_chunk_id,
            e.provenance_page
        FROM   concept_edges e
        JOIN   concepts c
               ON c.id = e.target_concept_id
              AND c.tenant_id = e.tenant_id
        WHERE  e.tenant_id = :tenant_id
          AND  e.source_concept_id = :concept_id
          AND  e.relation = :relation
          AND  e.confidence >= :min_confidence

        UNION ALL

        SELECT
            e.target_concept_id,
            c.name,
            c.slug,
            c.difficulty,
            p.depth + 1,
            p.path || e.target_concept_id,
            e.weight,
            e.confidence,
            e.verified,
            e.provenance_document_id,
            e.provenance_chunk_id,
            e.provenance_page
        FROM   prereqs p
        JOIN   concept_edges e
               ON e.source_concept_id = p.concept_id
              AND e.tenant_id = :tenant_id
              AND e.relation = :relation
              AND e.confidence >= :min_confidence
        JOIN   concepts c
               ON c.id = e.target_concept_id
              AND c.tenant_id = e.tenant_id
        WHERE  p.depth < :max_depth
          AND  NOT c.id = ANY(p.path)
    )
    SELECT concept_id, name, slug, difficulty, depth, path, weight, confidence, verified,
           provenance_document_id, provenance_chunk_id, provenance_page
    FROM (
        SELECT DISTINCT ON (concept_id)
            concept_id, name, slug, difficulty, depth, path, weight, confidence, verified,
            provenance_document_id, provenance_chunk_id, provenance_page
        FROM prereqs
        ORDER BY concept_id, depth, weight DESC
    ) AS shortest
    ORDER BY depth, difficulty, weight DESC
"""

_DEPENDENT_CLOSURE_SQL = """
    WITH RECURSIVE dependents AS (
        SELECT
            e.source_concept_id          AS concept_id,
            c.name,
            c.slug,
            c.difficulty,
            1                            AS depth,
            ARRAY[e.target_concept_id, e.source_concept_id] AS path,
            e.weight,
            e.confidence,
            e.verified,
            e.provenance_document_id,
            e.provenance_chunk_id,
            e.provenance_page
        FROM   concept_edges e
        JOIN   concepts c
               ON c.id = e.source_concept_id
              AND c.tenant_id = e.tenant_id
        WHERE  e.tenant_id = :tenant_id
          AND  e.target_concept_id = :concept_id
          AND  e.relation = :relation
          AND  e.confidence >= :min_confidence

        UNION ALL

        SELECT
            e.source_concept_id,
            c.name,
            c.slug,
            c.difficulty,
            p.depth + 1,
            p.path || e.source_concept_id,
            e.weight,
            e.confidence,
            e.verified,
            e.provenance_document_id,
            e.provenance_chunk_id,
            e.provenance_page
        FROM   dependents p
        JOIN   concept_edges e
               ON e.target_concept_id = p.concept_id
              AND e.tenant_id = :tenant_id
              AND e.relation = :relation
              AND e.confidence >= :min_confidence
        JOIN   concepts c
               ON c.id = e.source_concept_id
              AND c.tenant_id = e.tenant_id
        WHERE  p.depth < :max_depth
          AND  NOT c.id = ANY(p.path)
    )
    SELECT concept_id, name, slug, difficulty, depth, path, weight, confidence, verified,
           provenance_document_id, provenance_chunk_id, provenance_page
    FROM (
        SELECT DISTINCT ON (concept_id)
            concept_id, name, slug, difficulty, depth, path, weight, confidence, verified,
            provenance_document_id, provenance_chunk_id, provenance_page
        FROM dependents
        ORDER BY concept_id, depth, weight DESC
    ) AS shortest
    ORDER BY depth, difficulty, weight DESC
"""

_DIRECT_PREREQUISITES_SQL = """
    SELECT
        c.id            AS concept_id,
        c.name,
        c.slug,
        c.difficulty,
        e.weight,
        e.confidence,
        e.verified,
        e.cue,
        e.provenance_document_id,
        e.provenance_chunk_id,
        e.provenance_page,
        d.filename      AS source_document_title
    FROM   concept_edges e
    JOIN   concepts  c ON c.id = e.target_concept_id AND c.tenant_id = e.tenant_id
    LEFT   JOIN documents d ON d.id = e.provenance_document_id AND d.tenant_id = e.tenant_id
    WHERE  e.tenant_id = :tenant_id
      AND  e.source_concept_id = :concept_id
      AND  e.relation = :relation
      AND  e.confidence >= :min_confidence
    ORDER  BY e.verified DESC, e.weight DESC, c.difficulty
    LIMIT  :k
"""

_RELATED_SQL = """
    SELECT
        c.id AS concept_id,
        c.name,
        c.slug,
        c.difficulty,
        e.relation,
        e.confidence,
        e.verified,
        e.weight,
        e.provenance_chunk_id,
        e.provenance_page
    FROM   concept_edges e
    JOIN   concepts c
           ON  c.id = CASE WHEN e.source_concept_id = :concept_id
                           THEN e.target_concept_id
                           ELSE e.source_concept_id END
           AND c.tenant_id = e.tenant_id
    WHERE  e.tenant_id = :tenant_id
      AND  e.relation = 'related_to'
      AND  (e.source_concept_id = :concept_id OR e.target_concept_id = :concept_id)
      AND  e.confidence >= :min_confidence
    ORDER  BY e.confidence DESC, e.weight DESC
    LIMIT  :k
"""

_SIBLINGS_SQL = """
    WITH parent AS (
        SELECT e.target_concept_id AS parent_id
        FROM   concept_edges e
        WHERE  e.tenant_id = :tenant_id
          AND  e.source_concept_id = :concept_id
          AND  e.relation = 'part_of'
          AND  e.confidence >= :min_confidence
    )
    SELECT DISTINCT
        c.id AS concept_id, c.name, c.slug, c.difficulty, p.parent_id
    FROM   parent p
    JOIN   concept_edges e
           ON  e.target_concept_id = p.parent_id
          AND  e.tenant_id = :tenant_id
          AND  e.relation = 'part_of'
          AND  e.confidence >= :min_confidence
    JOIN   concepts c ON c.id = e.source_concept_id AND c.tenant_id = e.tenant_id
    WHERE  e.source_concept_id <> :concept_id
    ORDER  BY c.difficulty
    LIMIT  :k
"""

_DIRECT_RELATIONS_SQL = """
    SELECT
        c.id AS concept_id,
        c.name,
        c.slug,
        c.difficulty,
        e.relation,
        e.weight,
        e.confidence,
        e.verified,
        e.provenance_document_id,
        e.provenance_chunk_id,
        e.provenance_page
    FROM   concept_edges e
    JOIN   concepts c ON c.id = e.target_concept_id AND c.tenant_id = e.tenant_id
    WHERE  e.tenant_id = :tenant_id
      AND  e.source_concept_id = :concept_id
      AND  e.relation = :relation
      AND  e.confidence >= :min_confidence
    ORDER  BY e.verified DESC, e.weight DESC, c.difficulty
    LIMIT  :k
"""

_KNOWLEDGE_GAP_SQL = """
    WITH RECURSIVE prereqs AS (
        SELECT e.target_concept_id AS concept_id, 1 AS depth,
               ARRAY[e.source_concept_id, e.target_concept_id] AS path
        FROM   concept_edges e
        WHERE  e.tenant_id = :tenant_id
          AND  e.source_concept_id = :concept_id
          AND  e.relation = 'requires'
          AND  e.confidence >= :min_confidence
        UNION ALL
        SELECT e.target_concept_id, p.depth + 1, p.path || e.target_concept_id
        FROM   prereqs p
        JOIN   concept_edges e
               ON  e.source_concept_id = p.concept_id
              AND  e.tenant_id = :tenant_id
              AND  e.relation = 'requires'
              AND  e.confidence >= :min_confidence
        WHERE  p.depth < :max_depth
          AND  NOT e.target_concept_id = ANY(p.path)
    ),
    canonical AS (
        SELECT concept_id, MIN(depth) AS depth,
               (ARRAY_AGG(path ORDER BY depth))[1] AS path
        FROM   prereqs
        GROUP  BY concept_id
    ),
    evidence AS (
        SELECT key AS concept_key, value::numeric AS mastery
        FROM   JSONB_EACH_TEXT(CAST(:mastery AS jsonb))
    )
    SELECT
        c.id            AS concept_id,
        c.name,
        c.slug,
        c.difficulty,
        ca.depth,
        ca.path,
        COALESCE(ev.mastery, 0.0)     AS mastery,
        (ev.concept_key IS NULL)      AS never_assessed
    FROM   canonical ca
    JOIN   concepts c ON c.id = ca.concept_id AND c.tenant_id = :tenant_id
    LEFT   JOIN evidence ev ON ev.concept_key = c.id::text
    WHERE  COALESCE(ev.mastery, 0.0) < :mastery_threshold
    ORDER  BY ca.depth DESC, c.difficulty, never_assessed DESC
"""

_CYCLE_DETECTION_SQL = """
    WITH RECURSIVE walk AS (
        SELECT e.source_concept_id, e.target_concept_id,
               ARRAY[e.source_concept_id, e.target_concept_id] AS path,
               false AS is_cycle
        FROM   concept_edges e
        WHERE  e.tenant_id = :tenant_id
          AND  e.relation IN ('requires', 'part_of')
        UNION ALL
        SELECT w.source_concept_id, e.target_concept_id,
               w.path || e.target_concept_id,
               e.target_concept_id = ANY(w.path) AS is_cycle
        FROM   walk w
        JOIN   concept_edges e
               ON  e.source_concept_id = w.target_concept_id
              AND  e.tenant_id = :tenant_id
              AND  e.relation IN ('requires', 'part_of')
        WHERE  NOT w.is_cycle
          AND  ARRAY_LENGTH(w.path, 1) < :max_path_len
    )
    SELECT DISTINCT source_concept_id, target_concept_id, path
    FROM   walk
    WHERE  is_cycle
"""

_WOULD_CYCLE_SQL = """
    WITH RECURSIVE reach AS (
        SELECT e.target_concept_id AS id, 1 AS depth,
               ARRAY[:target_concept_id, e.target_concept_id] AS path
        FROM   concept_edges e
        WHERE  e.tenant_id = :tenant_id
          AND  e.source_concept_id = :target_concept_id
          AND  e.relation = :relation
        UNION ALL
        SELECT e.target_concept_id, r.depth + 1,
               r.path || e.target_concept_id
        FROM   reach r
        JOIN   concept_edges e
               ON  e.source_concept_id = r.id
              AND e.tenant_id = :tenant_id
              AND e.relation = :relation
        WHERE  r.depth < :max_depth
          AND  NOT e.target_concept_id = ANY(r.path)
    )
    SELECT EXISTS (SELECT 1 FROM reach WHERE id = :source_concept_id) AS would_cycle
"""

_CLOSURE_EDGES_SQL = """
    WITH RECURSIVE prereqs AS (
        SELECT CAST(:concept_id AS uuid) AS concept_id, 0 AS depth,
               ARRAY[CAST(:concept_id AS uuid)] AS path
        UNION ALL
        SELECT e.target_concept_id, p.depth + 1, p.path || e.target_concept_id
        FROM   prereqs p
        JOIN   concept_edges e
               ON  e.source_concept_id = p.concept_id
              AND e.tenant_id = :tenant_id
              AND e.relation = :relation
              AND e.confidence >= :min_confidence
        WHERE  p.depth < :max_depth
          AND  NOT e.target_concept_id = ANY(p.path)
    )
    SELECT DISTINCT e.source_concept_id, e.target_concept_id, e.verified, e.weight
    FROM   prereqs p
    JOIN   concept_edges e
           ON  e.source_concept_id = p.concept_id
          AND e.tenant_id = :tenant_id
          AND e.relation = :relation
          AND e.confidence >= :min_confidence
"""

_EXISTING_EDGE_SQL = """
    SELECT e.relation, e.verified, e.source_concept_id, e.target_concept_id
    FROM   concept_edges e
    WHERE  e.tenant_id = :tenant_id
      AND  (
            (e.source_concept_id = :source_concept_id AND e.target_concept_id = :target_concept_id)
         OR (e.source_concept_id = :target_concept_id AND e.target_concept_id = :source_concept_id)
      )
"""


class ConceptGraphRepository(TenantRepository[Concept]):
    """The single adapter over ``concepts``, ``concept_edges`` and their evidence."""

    model = Concept

    # -- concept resolution ------------------------------------------------
    async def get_by_slug(self, slug: str, *, course_id: uuid.UUID | None = None) -> Concept | None:
        stmt = self._select().where(Concept.slug == slug)
        if course_id is not None:
            stmt = stmt.where(Concept.course_id == course_id)
        else:
            stmt = stmt.order_by(Concept.created_at).limit(1)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def concepts_for_course(self, course_id: uuid.UUID, *, limit: int = 100) -> list[Concept]:
        """The tenant's concepts for one course, cheapest-first.

        The deterministic ``(difficulty, name)`` order is part of the contract:
        quiz generation samples this list, and an unstable order would make the
        same request produce different items.
        """
        stmt = (
            self._select()
            .where(Concept.course_id == course_id)
            .order_by(Concept.difficulty, Concept.name)
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def provenance_edges_for(
        self,
        *,
        chunk_ids: Sequence[uuid.UUID],
        document_ids: Sequence[uuid.UUID],
    ) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None, uuid.UUID | None]]:
        """Edges whose recorded provenance mentions one of the given chunks or documents.

        A bulk accessor: the caller assembles a whole result set and must not
        issue one edge query per passage. Returns plain tuples rather than ORM
        rows because only these four columns are ever read.
        """
        if not chunk_ids and not document_ids:
            return []
        stmt = select(
            ConceptEdge.source_concept_id,
            ConceptEdge.target_concept_id,
            ConceptEdge.provenance_chunk_id,
            ConceptEdge.provenance_document_id,
        ).where(
            ConceptEdge.tenant_id == self.tenant_id,
            or_(
                ConceptEdge.provenance_chunk_id.in_(chunk_ids),
                ConceptEdge.provenance_document_id.in_(document_ids),
            ),
        )
        return [
            (source, target, chunk_id, document_id)
            for source, target, chunk_id, document_id in (await self._session.execute(stmt)).all()
        ]

    async def resolve_concept(
        self,
        *,
        concept_id: uuid.UUID | None = None,
        concept_name: str | None = None,
        course_id: uuid.UUID | None = None,
    ) -> Concept | None:
        """Resolve by id, then slug, then case-insensitive name, then alias.

        Order matters: an exact identifier beats a spelling, and a spelling beats
        an alias, so an ambiguous alias can never shadow a direct name match.
        """
        if concept_id is not None:
            return await self.get(concept_id)
        if concept_name is None:
            return None
        slug = slugify(concept_name)
        if slug and is_valid_slug(slug):
            found = await self.get_by_slug(slug, course_id=course_id)
            if found is not None:
                return found
        name_stmt = self._select().where(func.lower(Concept.name) == concept_name.strip().lower())
        if course_id is not None:
            name_stmt = name_stmt.where(Concept.course_id == course_id)
        name_result = await self._session.execute(name_stmt.limit(1))
        found_by_name = name_result.scalars().first()
        if found_by_name is not None:
            return found_by_name

        alias_norm = normalise_alias(concept_name)
        alias_stmt = (
            select(Concept)
            .join(ConceptAlias, ConceptAlias.concept_id == Concept.id)
            .where(
                Concept.tenant_id == self.tenant_id,
                ConceptAlias.tenant_id == self.tenant_id,
                ConceptAlias.alias_norm == alias_norm,
            )
        )
        if course_id is not None:
            alias_stmt = alias_stmt.where(ConceptAlias.course_id == course_id)
        alias_result = await self._session.execute(alias_stmt.limit(1))
        return alias_result.scalars().first()

    # -- query 1: prerequisite closure ------------------------------------
    async def prerequisite_closure(
        self,
        concept_id: uuid.UUID,
        *,
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        relation: str = EdgeRelation.REQUIRES.value,
    ) -> list[ClosureNode]:
        """Everything the concept transitively requires, shallowest path wins.

        Terminates on a cyclic graph because every path is finite and the
        recursion refuses to revisit a node already on the current path.
        """
        relation = _validate_relation(relation)
        rows = (
            (
                await self._session.execute(
                    text(_PREREQUISITE_CLOSURE_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": concept_id,
                        "relation": relation,
                        "min_confidence": min_confidence,
                        "max_depth": max_depth,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_closure_node(row) for row in rows]

    async def dependent_closure(
        self,
        concept_id: uuid.UUID,
        *,
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        relation: str = EdgeRelation.REQUIRES.value,
    ) -> list[ClosureNode]:
        """Everything that transitively requires the concept (reverse traversal)."""
        relation = _validate_relation(relation)
        rows = (
            (
                await self._session.execute(
                    text(_DEPENDENT_CLOSURE_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": concept_id,
                        "relation": relation,
                        "min_confidence": min_confidence,
                        "max_depth": max_depth,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [_closure_node(row) for row in rows]

    # -- query 2: direct prerequisites ------------------------------------
    async def direct_prerequisites(
        self,
        concept_id: uuid.UUID,
        *,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        relation: str = EdgeRelation.REQUIRES.value,
        k: int = 50,
    ) -> list[DirectPrerequisite]:
        rows = (
            (
                await self._session.execute(
                    text(_DIRECT_PREREQUISITES_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": concept_id,
                        "relation": relation,
                        "min_confidence": min_confidence,
                        "k": k,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            DirectPrerequisite(
                concept_id=_as_uuid(row["concept_id"]),
                name=_as_str(row["name"]),
                slug=_as_str(row["slug"]),
                difficulty=_as_int(row["difficulty"]),
                weight=_as_float(row["weight"]),
                confidence=_as_float(row["confidence"]),
                verified=_as_bool(row["verified"]),
                cue=_as_str(row["cue"]),
                provenance_document_id=_as_optional_uuid(row["provenance_document_id"]),
                provenance_chunk_id=_as_optional_uuid(row["provenance_chunk_id"]),
                provenance_page=None
                if row["provenance_page"] is None
                else _as_int(row["provenance_page"]),
                source_document_title=_as_optional_str(row["source_document_title"]),
            )
            for row in rows
        ]

    # -- query 3: related concepts ----------------------------------------
    async def related_concepts(
        self,
        concept_id: uuid.UUID,
        *,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        k: int = 12,
    ) -> list[RelatedConcept]:
        """Direct ``related_to`` associations plus siblings under a shared parent.

        Neither is ever traversed recursively: ``related_to`` is non-transitive
        and chaining associations produces plausible nonsense (§3.3). Siblings
        are included because a sibling is frequently the *actual* gap.
        """
        params = {
            "tenant_id": self.tenant_id,
            "concept_id": concept_id,
            "min_confidence": min_confidence,
            "k": k,
        }
        related_rows = (await self._session.execute(text(_RELATED_SQL), params)).mappings().all()
        sibling_rows = (await self._session.execute(text(_SIBLINGS_SQL), params)).mappings().all()

        results: dict[uuid.UUID, RelatedConcept] = {}
        for row in related_rows:
            cid = _as_uuid(row["concept_id"])
            results[cid] = RelatedConcept(
                concept_id=cid,
                name=_as_str(row["name"]),
                slug=_as_str(row["slug"]),
                difficulty=_as_int(row["difficulty"]),
                relation=_as_str(row["relation"]),
                kind="related_to",
                weight=_as_float(row["weight"]),
                confidence=_as_float(row["confidence"]),
                verified=_as_bool(row["verified"]),
                provenance_chunk_id=_as_optional_uuid(row["provenance_chunk_id"]),
                provenance_page=None
                if row["provenance_page"] is None
                else _as_int(row["provenance_page"]),
            )
        for row in sibling_rows:
            cid = _as_uuid(row["concept_id"])
            results.setdefault(
                cid,
                RelatedConcept(
                    concept_id=cid,
                    name=_as_str(row["name"]),
                    slug=_as_str(row["slug"]),
                    difficulty=_as_int(row["difficulty"]),
                    relation=EdgeRelation.PART_OF.value,
                    kind="sibling",
                    weight=1.0,
                    confidence=0.0,
                    verified=False,
                    provenance_chunk_id=None,
                    provenance_page=None,
                ),
            )
        ordered = sorted(results.values(), key=lambda item: (item.difficulty, item.slug))
        return ordered[:k]

    async def direct_relations(
        self,
        concept_id: uuid.UUID,
        relation: str,
        *,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        k: int = 50,
    ) -> list[RelatedConcept]:
        """Outgoing edges of one non-transitive relation (``assesses``, etc.)."""
        rows = (
            (
                await self._session.execute(
                    text(_DIRECT_RELATIONS_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": concept_id,
                        "relation": relation,
                        "min_confidence": min_confidence,
                        "k": k,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            RelatedConcept(
                concept_id=_as_uuid(row["concept_id"]),
                name=_as_str(row["name"]),
                slug=_as_str(row["slug"]),
                difficulty=_as_int(row["difficulty"]),
                relation=_as_str(row["relation"]),
                kind="direct",
                weight=_as_float(row["weight"]),
                confidence=_as_float(row["confidence"]),
                verified=_as_bool(row["verified"]),
                provenance_chunk_id=_as_optional_uuid(row["provenance_chunk_id"]),
                provenance_page=None
                if row["provenance_page"] is None
                else _as_int(row["provenance_page"]),
            )
            for row in rows
        ]

    # -- query 4: knowledge gap -------------------------------------------
    async def knowledge_gap(
        self,
        user_mastery: Mapping[str | uuid.UUID, float],
        target_concept_id: uuid.UUID,
        *,
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        mastery_threshold: float = 0.6,
    ) -> list[KnowledgeGap]:
        """Prerequisites of the target for which there is no mastery evidence.

        ``user_mastery`` is supplied by the caller rather than read from a
        ``progress_events`` join: those tables are PR 13, and the graph repository
        must not depend on a schema that does not exist yet. Absence of evidence
        is mastery 0, but it is reported as ``never_assessed`` so the two cases
        are distinguishable.
        """
        mastery_json = json.dumps(
            {str(key): float(value) for key, value in user_mastery.items()},
            sort_keys=True,
        )
        rows = (
            (
                await self._session.execute(
                    text(_KNOWLEDGE_GAP_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": target_concept_id,
                        "min_confidence": min_confidence,
                        "max_depth": max_depth,
                        "mastery": mastery_json,
                        "mastery_threshold": mastery_threshold,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            KnowledgeGap(
                concept_id=_as_uuid(row["concept_id"]),
                name=_as_str(row["name"]),
                slug=_as_str(row["slug"]),
                difficulty=_as_int(row["difficulty"]),
                depth=_as_int(row["depth"]),
                path=_as_path(row["path"]),
                mastery=_as_float(row["mastery"]),
                never_assessed=_as_bool(row["never_assessed"]),
            )
            for row in rows
        ]

    # -- query 5: cycle detection -----------------------------------------
    async def detect_cycles(
        self,
        *,
        max_path_len: int = GRAPH_DEFAULT_MAX_PATH_LEN,
    ) -> list[CycleEdge]:
        """Enumerate cycles over the structural relations, for the review queue."""
        rows = (
            (
                await self._session.execute(
                    text(_CYCLE_DETECTION_SQL),
                    {"tenant_id": self.tenant_id, "max_path_len": max_path_len},
                )
            )
            .mappings()
            .all()
        )
        return [
            CycleEdge(
                source_concept_id=_as_uuid(row["source_concept_id"]),
                target_concept_id=_as_uuid(row["target_concept_id"]),
                path=_as_path(row["path"]),
            )
            for row in rows
        ]

    async def would_create_cycle(
        self,
        *,
        source_concept_id: uuid.UUID,
        target_concept_id: uuid.UUID,
        relation: str = EdgeRelation.REQUIRES.value,
        max_depth: int = GRAPH_DEFAULT_MAX_PATH_LEN,
    ) -> bool:
        """Whether ``source ->target`` would close a cycle (§3.7, measure 3)."""
        relation = _validate_relation(relation)
        result = await self._session.execute(
            text(_WOULD_CYCLE_SQL),
            {
                "tenant_id": self.tenant_id,
                "source_concept_id": source_concept_id,
                "target_concept_id": target_concept_id,
                "relation": relation,
                "max_depth": max_depth,
            },
        )
        return _as_bool(result.scalar_one())

    # -- roadmap -----------------------------------------------------------
    async def closure_edges(
        self,
        concept_id: uuid.UUID,
        *,
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        relation: str = EdgeRelation.REQUIRES.value,
    ) -> list[ClosureEdge]:
        """The edges whose endpoints lie inside the closure of ``concept_id``."""
        relation = _validate_relation(relation)
        rows = (
            (
                await self._session.execute(
                    text(_CLOSURE_EDGES_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "concept_id": concept_id,
                        "relation": relation,
                        "min_confidence": min_confidence,
                        "max_depth": max_depth,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            ClosureEdge(
                source_concept_id=_as_uuid(row["source_concept_id"]),
                target_concept_id=_as_uuid(row["target_concept_id"]),
                verified=_as_bool(row["verified"]),
                weight=_as_float(row["weight"]),
            )
            for row in rows
        ]

    async def plan_roadmap(
        self,
        goal_concept_id: uuid.UUID,
        mastered: Mapping[str | uuid.UUID, float],
        *,
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        mastery_threshold: float = 0.6,
        relation: str = EdgeRelation.REQUIRES.value,
        strict: bool = False,
    ) -> RoadmapPlan:
        """Kahn's algorithm with deterministic tie-breaking (§8.1).

        The next step is the available node with the smallest
        ``(difficulty, -verified, -weight, slug)`` key. When no node is available
        and nodes remain, a cycle exists: those nodes are returned as
        ``unmet_cycle`` rather than hidden, because that is exactly what the
        review UI needs. ``strict`` raises :class:`GraphCycleError` instead.
        """
        goal = await self.get(goal_concept_id)
        if goal is None:
            raise NotFoundError("Concept not found.")

        nodes = await self.prerequisite_closure(
            goal_concept_id,
            max_depth=max_depth,
            min_confidence=min_confidence,
            relation=relation,
        )
        edges = await self.closure_edges(
            goal_concept_id,
            max_depth=max_depth,
            min_confidence=min_confidence,
            relation=relation,
        )

        info: dict[uuid.UUID, ClosureNode] = {node.concept_id: node for node in nodes}
        info[goal.id] = ClosureNode(
            concept_id=goal.id,
            name=goal.name,
            slug=goal.slug,
            difficulty=goal.difficulty,
            depth=0,
            path=(goal.id,),
            weight=1.0,
            confidence=float(goal.confidence),
            verified=goal.verified,
            provenance_document_id=goal.provenance_document_id,
            provenance_chunk_id=goal.provenance_chunk_id,
            provenance_page=goal.provenance_page,
        )

        mastered_ids = _mastered_ids(mastered, mastery_threshold)
        eligible = {cid for cid in info if cid not in mastered_ids}
        pending: dict[uuid.UUID, set[uuid.UUID]] = {cid: set() for cid in eligible}
        for edge in edges:
            if edge.source_concept_id in eligible and edge.target_concept_id in eligible:
                pending[edge.source_concept_id].add(edge.target_concept_id)

        ordered: list[uuid.UUID] = []
        while pending:
            ready = [cid for cid, deps in pending.items() if not deps]
            if not ready:
                break
            nxt = min(ready, key=lambda cid: _roadmap_priority(info[cid]))
            ordered.append(nxt)
            del pending[nxt]
            for deps in pending.values():
                deps.discard(nxt)

        unmet = tuple(sorted(pending))
        if unmet and strict:
            raise GraphCycleError(
                f"The roadmap for {goal.slug!r} contains a cycle across {len(unmet)} concepts."
            )
        steps = tuple(
            RoadmapStep(
                concept_id=cid,
                name=info[cid].name,
                slug=info[cid].slug,
                difficulty=info[cid].difficulty,
                depth=info[cid].depth,
            )
            for cid in ordered
        )
        return RoadmapPlan(steps=steps, unmet_cycle=unmet)

    # -- upsert ------------------------------------------------------------
    async def upsert_edge(
        self,
        *,
        source_concept_id: uuid.UUID,
        target_concept_id: uuid.UUID,
        relation: str,
        confidence: float,
        weight: float,
        cue: str,
        provenance_document_id: uuid.UUID | None,
        provenance_chunk_id: uuid.UUID | None,
        provenance_page: int | None,
        source_quote: str | None,
        provenance_sources: Sequence[Mapping[str, Any]],
        extraction_run_id: uuid.UUID | None,
        prompt_version: str | None,
        model: str | None,
    ) -> bool:
        """Insert or merge one edge. Returns whether a row was written.

        The ``WHERE concept_edges.verified = false`` clause is what protects a
        human decision: a conflict against a verified row resolves to a no-op,
        no error is raised, and the caller queues the new evidence for review
        (§6.3). ``GREATEST`` keeps confidence monotone so an unrelated
        re-ingestion can never weaken a prerequisite.
        """
        insert_stmt = pg_insert(ConceptEdge).values(
            tenant_id=self.tenant_id,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            relation=relation,
            confidence=confidence,
            weight=weight,
            corroboration_count=1,
            cue=cue,
            provenance_document_id=provenance_document_id,
            provenance_chunk_id=provenance_chunk_id,
            provenance_page=provenance_page,
            source_quote=source_quote,
            provenance_sources=list(provenance_sources),
            extraction_run_id=extraction_run_id,
            prompt_version=prompt_version,
            model=model,
        )
        stmt = insert_stmt.on_conflict_do_update(
            constraint="concept_edges_unique",
            set_={
                "confidence": func.greatest(
                    ConceptEdge.confidence, insert_stmt.excluded.confidence
                ),
                "weight": func.greatest(ConceptEdge.weight, insert_stmt.excluded.weight),
                "corroboration_count": ConceptEdge.corroboration_count + 1,
                "cue": case(
                    (ConceptEdge.cue == "explicit", "explicit"),
                    else_=insert_stmt.excluded.cue,
                ),
                "provenance_sources": ConceptEdge.provenance_sources.op("||")(
                    insert_stmt.excluded.provenance_sources
                ),
                "updated_at": func.now(),
            },
            where=(ConceptEdge.verified.is_(False)),
        ).returning(ConceptEdge.id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    # -- evidence for the confidence model ---------------------------------
    async def existing_edge_states(
        self,
        source_concept_id: uuid.UUID,
        target_concept_id: uuid.UUID,
    ) -> list[tuple[str, bool, uuid.UUID, uuid.UUID]]:
        """Every edge between the two concepts, in either direction.

        The confidence model needs to know whether a verified edge already
        asserts the same relation, asserts a conflicting one, or whether a
        non-contradicting path exists. This is the raw material for the first
        two; the path question is answered by :meth:`would_create_cycle` with the
        arguments swapped.
        """
        rows = (
            (
                await self._session.execute(
                    text(_EXISTING_EDGE_SQL),
                    {
                        "tenant_id": self.tenant_id,
                        "source_concept_id": source_concept_id,
                        "target_concept_id": target_concept_id,
                    },
                )
            )
            .mappings()
            .all()
        )
        return [
            (
                _as_str(row["relation"]),
                _as_bool(row["verified"]),
                _as_uuid(row["source_concept_id"]),
                _as_uuid(row["target_concept_id"]),
            )
            for row in rows
        ]

    # -- tool adapter ------------------------------------------------------
    async def search_entities(
        self,
        *,
        concept_id: uuid.UUID | None = None,
        concept_name: str | None = None,
        relation: str | None = None,
        direction: str = "prerequisites",
        max_depth: int = GRAPH_DEFAULT_MAX_DEPTH,
        min_confidence: float = GRAPH_DEFAULT_MIN_CONFIDENCE,
        k: int = 50,
        course_id: uuid.UUID | None = None,
    ) -> GraphSearchOutcome:
        """Resolve a concept and return a typed closure, related set or direct edges.

        ``related_to`` is never traversed as a closure: when it is the requested
        relation the result is a bounded, depth-1 association list.
        """
        concept = await self.resolve_concept(
            concept_id=concept_id, concept_name=concept_name, course_id=course_id
        )
        if concept is None:
            return GraphSearchOutcome(
                entities=(), degraded=("knowledge_graph_empty",), depth_used=0
            )

        relation_value = None if relation is None else str(relation)
        if relation_value is not None and relation_value not in _CLOSURE_RELATION_VALUES:
            return await self._non_transitive_outcome(
                concept, relation_value, min_confidence=min_confidence, k=k
            )

        closure_relation = relation_value or EdgeRelation.REQUIRES.value
        entities: list[GraphEntityRow] = []
        if direction in {"prerequisites", "both"}:
            nodes = await self.prerequisite_closure(
                concept.id,
                max_depth=max_depth,
                min_confidence=min_confidence,
                relation=closure_relation,
            )
            entities.extend(_closure_entity(node, "prerequisite_of") for node in nodes)
        if direction in {"dependents", "both"}:
            dependents = await self.dependent_closure(
                concept.id,
                max_depth=max_depth,
                min_confidence=min_confidence,
                relation=closure_relation,
            )
            entities.extend(_closure_entity(node, "requires") for node in dependents)

        entities.sort(key=lambda entity: (entity.depth, entity.slug))
        limited = tuple(entities[:k])
        depth_used = max((entity.depth for entity in limited), default=0)
        degraded = () if limited else ("knowledge_graph_empty",)
        return GraphSearchOutcome(entities=limited, degraded=degraded, depth_used=depth_used)

    async def _non_transitive_outcome(
        self,
        concept: Concept,
        relation: str,
        *,
        min_confidence: float,
        k: int,
    ) -> GraphSearchOutcome:
        if relation == EdgeRelation.RELATED_TO.value:
            related = await self.related_concepts(concept.id, min_confidence=min_confidence, k=k)
            entities = tuple(
                GraphEntityRow(
                    concept_id=item.concept_id,
                    name=item.name,
                    slug=item.slug,
                    relation=item.relation,
                    depth=1,
                    direction="related_to",
                    weight=item.weight,
                    confidence=item.confidence,
                    verified=item.verified,
                    path=(concept.id, item.concept_id),
                    provenance={
                        "chunk_id": _optional_str(item.provenance_chunk_id),
                        "page": item.provenance_page,
                    },
                )
                for item in related
            )
        else:
            direct = await self.direct_relations(
                concept.id, relation, min_confidence=min_confidence, k=k
            )
            entities = tuple(
                GraphEntityRow(
                    concept_id=item.concept_id,
                    name=item.name,
                    slug=item.slug,
                    relation=item.relation,
                    depth=1,
                    direction="related_to",
                    weight=item.weight,
                    confidence=item.confidence,
                    verified=item.verified,
                    path=(concept.id, item.concept_id),
                    provenance={
                        "chunk_id": _optional_str(item.provenance_chunk_id),
                        "page": item.provenance_page,
                    },
                )
                for item in direct
            )
        degraded = () if entities else ("knowledge_graph_empty",)
        return GraphSearchOutcome(
            entities=entities, degraded=degraded, depth_used=1 if entities else 0
        )


def _optional_str(value: uuid.UUID | None) -> str | None:
    return None if value is None else str(value)


def _closure_node(row: RowMapping) -> ClosureNode:
    return ClosureNode(
        concept_id=_as_uuid(row["concept_id"]),
        name=_as_str(row["name"]),
        slug=_as_str(row["slug"]),
        difficulty=_as_int(row["difficulty"]),
        depth=_as_int(row["depth"]),
        path=_as_path(row["path"]),
        weight=_as_float(row["weight"]),
        confidence=_as_float(row["confidence"]),
        verified=_as_bool(row["verified"]),
        provenance_document_id=_as_optional_uuid(row["provenance_document_id"]),
        provenance_chunk_id=_as_optional_uuid(row["provenance_chunk_id"]),
        provenance_page=None if row["provenance_page"] is None else _as_int(row["provenance_page"]),
    )


def _closure_entity(node: ClosureNode, direction: str) -> GraphEntityRow:
    return GraphEntityRow(
        concept_id=node.concept_id,
        name=node.name,
        slug=node.slug,
        relation=EdgeRelation.REQUIRES.value,
        depth=node.depth,
        direction=direction,
        weight=node.weight,
        confidence=node.confidence,
        verified=node.verified,
        path=node.path,
        provenance={
            "document_id": _optional_str(node.provenance_document_id),
            "chunk_id": _optional_str(node.provenance_chunk_id),
            "page": node.provenance_page,
        },
    )


def _roadmap_priority(node: ClosureNode) -> tuple[int, int, float, str]:
    """Deterministic tie-break: difficulty, then human-verified, then weight, then slug."""
    return (node.difficulty, 0 if node.verified else 1, -node.weight, node.slug)


__all__ = [
    "GRAPH_DEFAULT_MAX_DEPTH",
    "GRAPH_DEFAULT_MAX_PATH_LEN",
    "GRAPH_DEFAULT_MIN_CONFIDENCE",
    "ClosureEdge",
    "ClosureNode",
    "ConceptGraphRepository",
    "CycleEdge",
    "DirectPrerequisite",
    "GraphCycleError",
    "GraphEntityRow",
    "GraphSearchOutcome",
    "KnowledgeGap",
    "RelatedConcept",
    "RoadmapPlan",
    "RoadmapStep",
]
