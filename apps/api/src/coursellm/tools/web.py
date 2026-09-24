"""``search_web_sources`` — allowlisted external lookups, off by default.

Two independent switches guard this tool, which is deliberate belt-and-braces:

* :func:`coursellm.tools.build_tool_registry` registers it **only** when
  ``AGENT_WEB_SEARCH_ENABLED`` is true. With the default ``false`` the name is
  not in the registry, so a model that emits it gets ``status="unknown_tool"``
  and there is no schema to bind to.
* :class:`~coursellm.tools.registry.ToolExecutor` refuses ``side_effects =
  "external"`` unless ``AGENT_ALLOW_EXTERNAL_TOOLS`` is true, so enabling one
  flag without the other still does not reach the network.

The handler itself is a typed stub: no outbound HTTP is performed by the agent
layer (``security.md`` section 5 item 9), and the allowlisted fetcher is PR 12.
It returns candidates only and never page bodies, which is what keeps an
external page from entering the evidence region as anything but delimited data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)


class SearchWebSourcesArgs(BaseModel):
    """Arguments for an allowlisted external lookup."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)
    allowlist_domains: list[str] | None = Field(default=None, max_length=20)
    k: int = Field(default=5, ge=1, le=20)


class WebSource(BaseModel):
    """One external candidate: a URL, a title and a snippet, never a page body."""

    url: str
    title: str
    snippet: str
    retrieved_at: str


class WebSourcesResult(BaseModel):
    """External candidates, or an empty list with a degradation reason."""

    sources: list[WebSource] = Field(default_factory=list)
    degraded: list[str] = Field(default_factory=list)


async def search_web_sources(args: SearchWebSourcesArgs, ctx: ToolContext) -> WebSourcesResult:
    """Return no external candidates; the allowlisted fetcher is PR 12."""
    return WebSourcesResult(sources=[], degraded=["external_sources_unavailable"])


def register(registry: ToolRegistry) -> None:
    registry.register(
        ToolSpec(
            name="search_web_sources",
            description=(
                "Allowlisted external sources for when the corpus is insufficient: "
                "url, title, snippet and retrieval time, never a full page body."
            ),
            parameters=SearchWebSourcesArgs,
            required_permissions=frozenset({Permission.WEB_READ}),
            side_effects="external",
            timeout_ms=8000,
            handler=search_web_sources,
        )
    )


__all__ = [
    "SearchWebSourcesArgs",
    "WebSource",
    "WebSourcesResult",
    "register",
    "search_web_sources",
]
