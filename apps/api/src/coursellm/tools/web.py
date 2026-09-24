"""``search_web_sources`` — allowlisted candidates from the curated catalogue.

Two independent switches guard this tool, exactly as before, which is deliberate
belt-and-braces:

* :func:`coursellm.tools.build_tool_registry` registers it **only** when
  ``AGENT_WEB_SEARCH_ENABLED`` is true. With the default ``false`` the name is
  not in the registry, so a model that emits it gets ``status="unknown_tool"``
  and there is no schema to bind to.
* :class:`~coursellm.tools.registry.ToolExecutor` refuses ``side_effects =
  "external"`` unless ``AGENT_ALLOW_EXTERNAL_TOOLS`` is true, so enabling one
  flag without the other still does not run the handler.

**No outbound HTTP is performed by this PR, on purpose.** Fetching an arbitrary
allowlisted page is an SSRF surface (redirects, DNS rebinding, private address
space) *and* a prompt-injection surface: the fetched body would enter the model's
context, and a page can be changed after it was reviewed. Doing it safely needs
its own review — egress controls, redirect pinning, size and content-type limits,
and delimiting the body as untrusted data — so it is deferred rather than rushed.

What the handler does instead is return **candidates from the curated catalogue**
whose host is on the explicit :data:`~coursellm.recommend.seed.SOURCE_TRUST_BY_DOMAIN`
allowlist. A caller may narrow the allowlist but can never widen it: the requested
domains are intersected with the curated set, so a request naming ``evil.example``
selects nothing. When outbound fetching is added, this allowlist is the control
that will make it safe — the URL that will be fetched must already have passed it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from coursellm.recommend.catalogue import ResourceCatalogue
from coursellm.recommend.seed import SOURCE_TRUST_BY_DOMAIN, domain_for_host, host_of
from coursellm.tools.registry import (
    Permission,
    ToolContext,
    ToolRegistry,
    ToolSpec,
)

#: Rows scanned before the allowlist filter is applied. The catalogue is small
#: and curated; the bound keeps a broad query from reading everything.
SCAN_LIMIT = 500
#: Characters of the catalogue description returned as the snippet. A snippet is
#: a pointer, not a page: the caller follows the URL deliberately.
SNIPPET_CHARS = 280

#: The default allowlist is exactly the curated trust mapping's domains.
DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = tuple(sorted(SOURCE_TRUST_BY_DOMAIN))


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


def is_allowlisted(url: str) -> bool:
    """Whether ``url``'s host is on the curated allowlist."""
    return domain_for_host(host_of(url)) is not None


def effective_domains(requested: list[str] | None) -> frozenset[str]:
    """Narrow the allowlist; a caller can never widen it.

    ``None`` means "the whole curated allowlist". A requested domain is kept only
    when it is itself a curated domain, so the result is always a subset.
    """
    if requested is None:
        return frozenset(DEFAULT_ALLOWED_DOMAINS)
    return frozenset(domain for domain in requested if domain in SOURCE_TRUST_BY_DOMAIN)


def _host_in_domains(url: str, domains: frozenset[str]) -> bool:
    host = host_of(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


async def search_web_sources(args: SearchWebSourcesArgs, ctx: ToolContext) -> WebSourcesResult:
    """Return allowlisted catalogue candidates; no page is fetched.

    ``outbound_fetch_deferred`` is always present in ``degraded`` so a caller can
    tell that the snippet is catalogue metadata rather than the live page.
    """
    degraded = ["outbound_fetch_deferred"]
    if ctx.session is None:
        degraded.append("no_catalogue_session")
        return WebSourcesResult(sources=[], degraded=degraded)

    domains = effective_domains(args.allowlist_domains)
    if not domains:
        degraded.append("no_allowlisted_domains")
        return WebSourcesResult(sources=[], degraded=degraded)

    catalogue = ResourceCatalogue(ctx.session)
    page = await catalogue.search(query=args.query, limit=SCAN_LIMIT)
    retrieved_at = datetime.now(UTC).isoformat()
    sources = [
        WebSource(
            url=resource.url,
            title=resource.title,
            snippet=resource.description[:SNIPPET_CHARS],
            retrieved_at=retrieved_at,
        )
        for resource in page.items
        if _host_in_domains(resource.url, domains)
    ][: args.k]

    if not sources:
        degraded.append("no_allowlisted_source_matches")
    return WebSourcesResult(sources=sources, degraded=degraded)


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
    "DEFAULT_ALLOWED_DOMAINS",
    "SCAN_LIMIT",
    "SNIPPET_CHARS",
    "SearchWebSourcesArgs",
    "WebSource",
    "WebSourcesResult",
    "effective_domains",
    "is_allowlisted",
    "register",
    "search_web_sources",
]
