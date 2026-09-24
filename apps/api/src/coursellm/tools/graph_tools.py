"""``search_knowledge_graph`` — the prerequisite-closure tool.

The real traversal is PR 10. This module ships the typed contract and the
plumbing so the graph node, the permission matrix and the degradation paths are
exercised now, and a later PR replaces only :class:`NullKnowledgeGraphRepository`
with the real repository. The null implementation returns an empty closure and
sets ``degraded=["knowledge_graph_empty"]`` rather than inventing concepts: an
empty prerequisite set is a truthful answer to "what is not yet modelled", and a
fabricated one would silently corrupt a roadmap.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

Direction = Literal["prerequisites", "dependents", "both"]


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
    """The repository contract PR 10 implements."""

    async def search(
        self,
        *,
        tenant_id: uuid.UUID,
        args: SearchKnowledgeGraphArgs,
    ) -> KnowledgeGraphResult:
        """Return the closure for ``args``, or an empty result with a reason."""
        ...


class NullKnowledgeGraphRepository:
    """The PR 10 placeholder: a truthful empty closure, never fabricated edges."""

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


def make_handler(
    repository: KnowledgeGraphRepository,
) -> Any:
    """Build the handler bound to ``repository``."""

    async def search_knowledge_graph(
        args: SearchKnowledgeGraphArgs, ctx: ToolContext
    ) -> KnowledgeGraphResult:
        return await repository.search(tenant_id=ctx.tenant_id, args=args)

    return search_knowledge_graph


def register(registry: ToolRegistry, *, repository: KnowledgeGraphRepository | None = None) -> None:
    """Register the graph tool with the given repository (or the null one)."""
    active = repository or NullKnowledgeGraphRepository()
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
            handler=make_handler(active),
        )
    )


__all__ = [
    "Direction",
    "KnowledgeGraphRepository",
    "KnowledgeGraphResult",
    "NullKnowledgeGraphRepository",
    "SearchKnowledgeGraphArgs",
    "make_handler",
    "register",
]
