"""Token and cost accounting for model calls.

Cost figures here are **operational estimates**, not invoices. The two sources
are tried in order:

1. ``litellm.cost_per_token`` — authoritative while its price map is current.
2. A small static table keyed by a substring match — the safety net for models
   LiteLLM has no price for, including brand-new or self-hosted ids.

If neither knows the model the cost is ``0.0`` and the result is marked
``priced=False``. That distinction is the point: a dashboard that summed only
costs it could price and presented them as a total would understate spend and
hide the gap. An explicit "unpriced" count lets coverage be shown honestly, so
the total can be labelled partial instead of wrong.

Both public functions are pure and must never raise. Accounting runs on the
request path and on failure paths; a pricing lookup that throws would turn a
successful answer into an error, which is the wrong trade every time. Errors are
therefore swallowed and degrade to the approximation below.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

# Fallback prices in USD per **one million** tokens, as
# ``(substring, prompt_price, completion_price)``. Ordered so that a shorter,
# more general name cannot shadow a longer, more specific one: ``gpt-4o-mini``
# is checked before ``gpt-4o``. Values were taken from public list prices and
# are expected to drift; that drift is why LiteLLM's map is tried first.
_STATIC_PRICES_USD_PER_MILLION: tuple[tuple[str, float, float], ...] = (
    ("gpt-4o-mini", 0.15, 0.60),
    ("gpt-4o", 2.50, 10.00),
    ("gpt-4.1-mini", 0.40, 1.60),
    ("gpt-4.1", 2.00, 8.00),
    ("o4-mini", 1.10, 4.40),
    ("claude-3-5-haiku", 0.80, 4.00),
    ("claude-3-5-sonnet", 3.00, 15.00),
    ("claude-3-haiku", 0.25, 1.25),
    ("gemini-1.5-flash", 0.075, 0.30),
    ("gemini-1.5-pro", 1.25, 5.00),
    ("llama-3.3-70b", 0.59, 0.79),
)

# Rough characters-per-token ratio used only when tiktoken is unavailable or
# cannot encode the text. English prose averages close to four; the value is
# documented rather than tuned because an approximate token count is only ever
# used for budgeting, never for billing.
_CHARS_PER_TOKEN = 4

PriceSource = Literal["litellm", "static", "unknown"]

# Encoding objects are expensive to build and safe to share, so they are cached
# per base model name. The cache is an implementation detail and does not make
# the functions impure: the same input always yields the same output.
_ENCODING_CACHE: dict[str, Any] = {}


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """A priced (or explicitly unpriced) token cost."""

    cost_usd: float
    priced: bool
    source: PriceSource


def _non_negative_int(value: float | int | None) -> int:
    """Coerce anything numeric-ish to a non-negative int without raising."""
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(number, 0)


def _static_price(model: str) -> tuple[float, float] | None:
    lowered = (model or "").lower()
    for substring, prompt_price, completion_price in _STATIC_PRICES_USD_PER_MILLION:
        if substring in lowered:
            return prompt_price, completion_price
    return None


def estimate_cost_breakdown(
    model: str, prompt_tokens: float | int | None, completion_tokens: float | int | None
) -> CostEstimate:
    """Price a call, reporting where the number came from and whether it exists.

    Never raises. An unknown model yields ``cost_usd=0.0`` with
    ``priced=False`` so that coverage can be reported rather than assumed.
    """
    prompt = _non_negative_int(prompt_tokens)
    completion = _non_negative_int(completion_tokens)

    # Pricing must never break the request path, so any failure here simply
    # leaves ``litellm_total`` as None and the static table is tried instead.
    litellm_total: float | None = None
    try:
        import litellm

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=model or "",
            prompt_tokens=prompt,
            completion_tokens=completion,
        )
        candidate = float(prompt_cost) + float(completion_cost)
        if candidate > 0.0 and math.isfinite(candidate):
            litellm_total = candidate
    except Exception:
        litellm_total = None
    if litellm_total is not None:
        return CostEstimate(cost_usd=litellm_total, priced=True, source="litellm")

    static = _static_price(model)
    if static is not None:
        prompt_price, completion_price = static
        total = (prompt / 1_000_000) * prompt_price + (completion / 1_000_000) * completion_price
        return CostEstimate(cost_usd=total, priced=True, source="static")

    return CostEstimate(cost_usd=0.0, priced=False, source="unknown")


def estimate_cost(
    model: str, prompt_tokens: float | int | None, completion_tokens: float | int | None
) -> float:
    """Return the estimated USD cost of a call, or ``0.0`` if it cannot be priced.

    Use :func:`estimate_cost_breakdown` when the ``priced`` flag is needed —
    which it is anywhere a total is displayed, because an unpriced call is not a
    free call.
    """
    return estimate_cost_breakdown(model, prompt_tokens, completion_tokens).cost_usd


def _encoding_for(model: str) -> Any | None:
    """Return a cached tiktoken encoding, or ``None`` if tiktoken cannot be used."""
    base_name = (model or "").strip().rsplit("/", 1)[-1] or "cl100k_base"
    if base_name in _ENCODING_CACHE:
        return _ENCODING_CACHE[base_name]
    try:
        import tiktoken
    except Exception:
        # tiktoken is not installed; the caller falls back to the approximation.
        return None
    try:
        encoding = tiktoken.encoding_for_model(base_name)
    except Exception:
        # The model is not in tiktoken's registry (a Claude or Gemini id, say),
        # so use a general-purpose encoding rather than failing the count.
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            # No bundled encoding and no network to fetch one.
            return None
    _ENCODING_CACHE[base_name] = encoding
    return encoding


def count_tokens(text: str, model: str = "") -> int:
    """Count tokens in ``text``, falling back to a documented approximation.

    Uses tiktoken when it is importable and can encode the text; otherwise
    returns ``ceil(len(text) / 4)``. The fallback exists so that an air-gapped
    or unusual environment degrades to a slightly wrong number rather than an
    exception, and the approximation is never used for billing.
    """
    if not text:
        return 0
    try:
        encoding = _encoding_for(model)
        if encoding is not None:
            return len(encoding.encode(text))
    except Exception:
        # Any failure at all degrades to the character approximation.
        return math.ceil(len(text) / _CHARS_PER_TOKEN)
    return math.ceil(len(text) / _CHARS_PER_TOKEN)
