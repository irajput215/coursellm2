"""The LiteLLM model gateway.

Every model call in the platform goes through this package, and through
:class:`~coursellm.llm.gateway.LiteLLMGateway` in particular. That single funnel
is what makes retries, ordered fallbacks, timeouts, structured-output validation
and usage accounting behave the same everywhere instead of being re-implemented
per call site (ADR-0007).

Re-exports are resolved lazily. The database model imports
:mod:`coursellm.llm.types`, so this package must be importable without pulling in
the gateway (which imports the ORM). A PEP 562 ``__getattr__`` keeps
``from coursellm.llm import LLMGateway`` working without creating that cycle or
importing the heavy provider library at database-import time.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

# Public name -> defining submodule.
_EXPORTS: dict[str, str] = {
    "ChatMessage": "types",
    "LLMRequest": "types",
    "LLMResponse": "types",
    "LLMScope": "types",
    "ModelTask": "types",
    "StructuredT": "types",
    "UsageRecord": "types",
    "bind_llm_scope": "types",
    "current_llm_scope": "types",
    "resolve_model": "routing",
    "fallback_chain": "routing",
    "provider_of": "routing",
    "CostEstimate": "cost",
    "count_tokens": "cost",
    "estimate_cost": "cost",
    "estimate_cost_breakdown": "cost",
    "LLMGateway": "gateway",
    "LiteLLMGateway": "gateway",
    "EchoGateway": "gateway",
    "get_gateway": "gateway",
    "reset_gateway_cache": "gateway",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Resolve a public name from its submodule on first access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module = import_module(f"coursellm.llm.{module_name}")
    return getattr(module, name)
