"""``search_knowledge_graph`` — the prerequisite-closure tool.

The traversal is real: the handler builds a
:class:`~coursellm.graph.repository.ConceptGraphRepository` over the caller's
tenant-scoped session and returns a typed closure with depth, path, confidence,
verified and provenance for every entity.

Two guarantees are enforced here rather than trusted to the model:

* **``related_to`` is never traversed for a closure.** When the caller asks for
  it, the result is a bounded depth-1 association list. Chaining `related_to` is
  how plausible nonsense is produced.
* **Low-confidence edges are excluded.** The effective confidence floor is the
  greater of the caller's ``min_confidence`` and
  ``GRAPH_MIN_TRAVERSABLE_CONFIDENCE``, so a model that passes ``0.0`` cannot
  widen the traversal. Inspecting unverified/review-queue edges requires the
  ``graph:review`` capability, which no agent holds (it is a declared
  :class:`~coursellm.tools.registry.Permission` member, granted to no agent).

``NullKnowledgeGraphRepository`` remains as the honest empty answer when there is
no session at all: an empty prerequisite set is a truthful statement about a graph
that is not modelled, and a fabricated one would silently corrupt a roadmap.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from coursellm.core.config import Settings
from coursellm.db.tenancy import TenantScope
from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

Direction = Literal["prerequisites", "dependents", "both"]

#: The capability required to see review-queue edges in traversal. It is a
#: first-class :class:`~coursellm.tools.registry.Permission` member and no agent
#: holds it, so a model-chosen tool argument can never widen traversal to the
#: review queue; the review UI supplies it explicitly.
REVIEW_PERMISSION = Permission.GRAPH_REVIEW


class SearchKnowledgeGraphArgs(BaseModel):
    """Arguments for a graph closure. ``tenant_id`` is never one of them."""

    model_config = ConfigDict(extra="forbid")

    concept_id: uuid.UUID | None = None
    concept_name: str | None = Field(default=None, max_length=200)
    relation: str | None = Field(default=None, max_length=60)
    direction: Direction = "prerequisites"
    max_depth: int = Field(default=3, ge=1, le=10)
    min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    limit: int = Field(default=50, ge=1, le=200)


class KnowledgeGraphResult(BaseModel):
    """A typed prerequisite closure and how it was produced."""

    entities: list[dict[str, Any]] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)
    depth_used: int = 0


class KnowledgeGraphRepository(Protocol):
    """The repository contract the agent layer binds to."""

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        args: SearchKnowledgeGraphArgs,
    ) -> KnowledgeGraphResult:
        """Return the closure for ``args``, or an empty result with a reason."""
        ...


class NullKnowledgeGraphRepository:
    """The truthful empty closure. Never fabricates edges."""

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        args: SearchKnowledgeGraphArgs,
    ) -> KnowledgeGraphResult:
        return KnowledgeGraphResult(
            entities=[],
            degraded=["knowledge_graph_empty"],
            depth_used=0,
        )


class PostgresKnowledgeGraphRepository:
    """The real closure, over a tenant-scoped session.

    ``include_below_threshold`` is a server-side construction flag, never a tool
    argument: it models the ``graph:review`` capability and defaults to off.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        include_below_threshold: bool = False,
    ) -> None:
        self._session = session
        self._settings = settings
        self._include_below_threshold = include_below_threshold

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        args: SearchKnowledgeGraphArgs,
    ) -> KnowledgeGraphResult:
        from coursellm.graph.repository import ConceptGraphRepository

        repo = ConceptGraphRepository(self._session, TenantScope(tenant_id))
        floor = self._settings.graph_min_traversable_confidence
        effective_floor = (
            args.min_confidence
            if self._include_below_threshold
            else max(args.min_confidence, floor)
        )
        outcome = await repo.search_entities(
            concept_id=args.concept_id,
            concept_name=args.concept_name,
            relation=args.relation,
            direction=args.direction,
            max_depth=args.max_depth,
            min_confidence=effective_floor,
            k=args.limit,
        )
        entities = [
            {
                "concept_id": str(entity.concept_id),
                "name": entity.name,
                "slug": entity.slug,
                "relation": entity.relation,
                "depth": entity.depth,
                "direction": entity.direction,
                "weight": entity.weight,
                "confidence": entity.confidence,
                "verified": entity.verified,
                "path": [str(step) for step in entity.path],
                "provenance": entity.provenance,
            }
            for entity in outcome.entities
        ]
        return KnowledgeGraphResult(
            entities=entities,
            degraded=list(outcome.degraded),
            depth_used=outcome.depth_used,
        )


def make_handler(
    repository: KnowledgeGraphRepository | None = None,
    *,
    include_below_threshold: bool = False,
) -> Any:
    """Build the handler.

    With an explicit repository the handler uses it; without one it builds the
    real PostgreSQL repository from the tool context's session, and degrades to
    the truthful empty closure when there is no session (unit tests, degraded
    deployments).
    """

    async def search_knowledge_graph(
        args: SearchKnowledgeGraphArgs, ctx: ToolContext
    ) -> KnowledgeGraphResult:
        active: KnowledgeGraphRepository
        if repository is not None:
            active = repository
        elif ctx.session is not None:
            active = PostgresKnowledgeGraphRepository(
                ctx.session,
                ctx.settings,
                include_below_threshold=include_below_threshold,
            )
        else:
            active = NullKnowledgeGraphRepository()
        return await active.search(tenant_id=ctx.tenant_id, args=args)

    return search_knowledge_graph


def register(
    registry: ToolRegistry,
    *,
    repository: KnowledgeGraphRepository | None = None,
    include_below_threshold: bool = False,
) -> None:
    """Register the graph tool with the given repository (or the real one)."""
    registry.register(
        ToolSpec(
            name="search_knowledge_graph",
            description=(
                "Prerequisite closure, related concepts and gap detection from the "
                "knowledge graph, with depth, confidence and provenance."
            ),
            parameters=SearchKnowledgeGraphArgs,
            required_permissions=frozenset({Permission.GRAPH_READ}),
            side_effects="none",
            timeout_ms=3000,
            handler=make_handler(repository, include_below_threshold=include_below_threshold),
        )
    )


__all__ = [
    "REVIEW_PERMISSION",
    "Direction",
    "KnowledgeGraphRepository",
    "KnowledgeGraphResult",
    "NullKnowledgeGraphRepository",
    "PostgresKnowledgeGraphRepository",
    "SearchKnowledgeGraphArgs",
    "make_handler",
    "register",
]
