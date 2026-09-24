"""Task-to-model resolution and the fallback chain.

Routing is intentionally pure and synchronous so that it can be unit-tested
without a provider, a database or an event loop, and so that a misconfiguration
is a startup or first-call failure rather than an intermittent one.

Two behaviours are worth stating plainly:

* **Routing can be turned off, not misconfigured away.** With
  ``llm_routing_enabled=False`` every task resolves to ``primary_model``. This
  is the escape hatch for debugging a single model without deleting the routing
  table.
* **An empty model is an error, never a silent default.** LiteLLM would fail
  deep inside a provider call with an opaque message; raising here names the
  setting instead.
"""

from __future__ import annotations

from coursellm.core.config import Settings
from coursellm.core.errors import ValidationError
from coursellm.llm.types import ModelTask

# LiteLLM addresses a model as ``<provider>/<model>``. The provider is the text
# before the first slash, which is also what appears in cost and usage records.
_UNKNOWN_PROVIDER = "unknown"


def resolve_model(settings: Settings, task: ModelTask) -> str:
    """Resolve a logical task to a provider model id.

    ``CLASSIFICATION`` and ``EXTRACTION`` are short, high-volume, low-reasoning
    calls, so they use the cheap model. ``REASONING`` is the opposite.
    ``TUTORING`` is the user-facing answer and uses the primary model.
    """
    if not settings.llm_routing_enabled:
        candidate = settings.primary_model
    elif task == ModelTask.CLASSIFICATION or task == ModelTask.EXTRACTION:
        candidate = settings.fast_model
    elif task == ModelTask.TUTORING:
        candidate = settings.primary_model
    else:
        candidate = settings.reasoning_model

    model = (candidate or "").strip()
    if not model:
        msg = (
            f"No model is configured for task {task.value!r}. "
            f"Set the corresponding model setting; an empty model id would fail "
            f"inside the provider call with no indication of which setting is wrong."
        )
        raise ValidationError(msg)
    return model


def fallback_chain(settings: Settings, task: ModelTask) -> list[str]:
    """Return the ordered, de-duplicated models to try for ``task``.

    The primary always comes first. Blank entries are dropped rather than
    passed to LiteLLM, and a fallback equal to the primary is removed: retrying
    the same broken model under a different label is not a fallback.
    """
    candidates = [resolve_model(settings, task), settings.fallback_model]
    chain: list[str] = []
    for candidate in candidates:
        model = (candidate or "").strip()
        if model and model not in chain:
            chain.append(model)
    return chain


def provider_of(model: str) -> str:
    """Return the LiteLLM provider prefix of ``model``.

    ``"openai/gpt-4o-mini"`` -> ``"openai"``; ``"gpt-4o-mini"`` -> ``"unknown"``
    because no provider was stated. A model with more than one slash still
    reports only the first segment (``"openai/gpt-4o/mini"`` -> ``"openai"``).
    """
    candidate = (model or "").strip()
    if "/" not in candidate:
        return _UNKNOWN_PROVIDER
    provider = candidate.split("/", 1)[0].strip()
    return provider or _UNKNOWN_PROVIDER
