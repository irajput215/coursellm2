"""Optional LangSmith tracing, gated by configuration and off by default.

LangSmith is a third-party sink: enabling it sends prompt and completion content
to a vendor. That is a data-protection decision, so the module is built so that
the *default* path imports nothing from ``langsmith``, makes no network call and
returns no-op objects whose methods do nothing. A request never pays for a
disabled integration because the disabled branch does not even import the SDK.

When it is enabled, the same ``capture_prompts_in_traces`` flag that governs OTel
governs payload content here: with the flag off, a run carries metadata (graph,
node, prompt version, config version, token counts) and no prompt text. The run
name convention is the document's:
``{graph}:{node}:{prompt_version}:{config_version}``, so an evaluated run records
the exact prompt and retrieval configuration that produced it.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from coursellm.core.config import Settings
from coursellm.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LangSmithHandle:
    """Whether LangSmith is active and, if so, where runs are written.

    ``capture_prompts`` is copied from settings at configure time so a call site
    cannot accidentally consult a different flag than the one that enabled the
    integration.
    """

    enabled: bool
    project: str | None = None
    capture_prompts: bool = False
    endpoint: str | None = None


def configure_langsmith(settings: Settings) -> LangSmithHandle:
    """Enable LangSmith only when both the switch and a key are present.

    A missing key with ``LANGSMITH_TRACING=true`` is a warning rather than a
    crash: the request path must still serve, and the misconfiguration is visible
    in the logs. Nothing is imported here; the SDK import happens on first use.
    """
    if not settings.langsmith_enabled:
        return LangSmithHandle(enabled=False, capture_prompts=False)
    if not settings.langsmith_api_key.strip():
        logger.warning(
            "langsmith_enabled_without_key",
            impact="LangSmith tracing stays off; set LANGSMITH_API_KEY to enable it.",
        )
        return LangSmithHandle(enabled=False, capture_prompts=False)
    return LangSmithHandle(
        enabled=True,
        project=settings.langsmith_project,
        capture_prompts=settings.capture_prompts_in_traces,
        endpoint=settings.langsmith_endpoint,
    )


def run_name(
    *,
    graph: str,
    node: str,
    prompt_version: str = "",
    config_version: str = "",
) -> str:
    """``{graph}:{node}:{prompt_version}:{config_version}`` from section 7."""
    return f"{graph}:{node}:{prompt_version}:{config_version}"


def payload(
    handle: LangSmithHandle,
    *,
    metadata: Mapping[str, Any] | None = None,
    prompts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a run's inputs, attaching prompt content only when permitted.

    This is the single place that decides whether prompt text crosses the vendor
    boundary, so the ``capture_prompts_in_traces=false`` guarantee is one branch
    rather than a rule every caller must remember.
    """
    run_inputs: dict[str, Any] = dict(metadata or {})
    if handle.capture_prompts and prompts:
        run_inputs["prompts"] = dict(prompts)
    return run_inputs


@contextmanager
def trace_run(
    handle: LangSmithHandle,
    *,
    name: str,
    run_type: str = "chain",
    metadata: Mapping[str, Any] | None = None,
    prompts: Mapping[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Open a LangSmith run for the block, or a no-op when disabled.

    A failure inside the LangSmith SDK is logged and swallowed. Observability is
    not allowed to turn a served answer into an error, the same rule the usage
    accountant follows.
    """
    if not handle.enabled:
        yield None
        return

    run = _start_run(
        handle,
        name=name,
        run_type=run_type,
        inputs=payload(handle, metadata=metadata, prompts=prompts),
    )
    try:
        yield run
    finally:
        _finish_run(run)


@contextmanager
def trace_node(
    handle: LangSmithHandle,
    *,
    graph: str,
    node: str,
    prompt_version: str = "",
    config_version: str = "",
    metadata: Mapping[str, Any] | None = None,
    prompts: Mapping[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Trace one graph node under the document's run name convention."""
    with trace_run(
        handle,
        name=run_name(
            graph=graph,
            node=node,
            prompt_version=prompt_version,
            config_version=config_version,
        ),
        run_type="chain",
        metadata={**(metadata or {}), "graph": graph, "node": node},
        prompts=prompts,
    ) as run:
        yield run


def _start_run(
    handle: LangSmithHandle,
    *,
    name: str,
    run_type: str,
    inputs: dict[str, Any],
) -> Any | None:
    try:
        from langsmith import RunTree

        run = RunTree(
            name=name,
            run_type=run_type,
            inputs=inputs,
            project_name=handle.project,
        )
        run.post()
    except Exception as exc:  # the vendor must never fail a request
        logger.warning("langsmith_run_failed", error_type=type(exc).__name__)
        return None
    return run


def _finish_run(run: Any | None) -> None:
    if run is None:
        return
    try:
        run.end()
        run.patch()
    except Exception as exc:  # pragma: no cover - vendor failure path
        logger.warning("langsmith_run_update_failed", error_type=type(exc).__name__)


__all__ = [
    "LangSmithHandle",
    "configure_langsmith",
    "payload",
    "run_name",
    "trace_node",
    "trace_run",
]
