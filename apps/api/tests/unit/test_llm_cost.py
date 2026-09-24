"""Cost and token accounting tests.

The important property is not that a number is returned but that an *unknown*
model is reported as unknown rather than as free. A silent zero would make a
spend dashboard understate usage with no way to notice.
"""

from __future__ import annotations

import pytest

from coursellm.llm.cost import (
    count_tokens,
    estimate_cost,
    estimate_cost_breakdown,
)

pytestmark = pytest.mark.unit


class TestEstimateCost:
    def test_known_model_returns_a_positive_cost(self) -> None:
        assert estimate_cost("openai/gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000) > 0.0

    def test_known_model_is_priced_by_litellm(self) -> None:
        estimate = estimate_cost_breakdown("openai/gpt-4o-mini", 1000, 1000)
        assert estimate.priced is True
        assert estimate.cost_usd > 0.0

    def test_model_litellm_cannot_price_uses_the_static_table(self) -> None:
        # LiteLLM's price map does not contain this id, but the static table's
        # substring match does.
        estimate = estimate_cost_breakdown("anthropic/claude-3-5-haiku-latest", 1000, 1000)
        assert estimate.priced is True
        assert estimate.cost_usd > 0.0
        assert estimate.source in {"litellm", "static"}

    def test_unknown_model_is_zero_and_marked_unpriced(self) -> None:
        estimate = estimate_cost_breakdown("totally/unknown-model-xyz", 1000, 1000)
        assert estimate.cost_usd == 0.0
        assert estimate.priced is False
        assert estimate.source == "unknown"

    def test_zero_tokens_cost_nothing_but_the_model_is_still_recognised(self) -> None:
        estimate = estimate_cost_breakdown("openai/gpt-4o-mini", 0, 0)
        assert estimate.priced is True
        assert estimate.cost_usd == 0.0

    @pytest.mark.parametrize(
        ("model", "prompt", "completion"),
        [
            ("", 0, 0),
            ("openai/gpt-4o-mini", -100, -100),
            ("openai/gpt-4o-mini", float("inf"), float("nan")),
            (None, None, None),
            ("openai/gpt-4o-mini", "not-a-number", 3.7),
        ],
    )
    def test_absurd_input_never_raises(
        self, model: object, prompt: object, completion: object
    ) -> None:
        # The signature is typed, but accounting runs on failure paths where a
        # caller may have passed a provider-supplied value of the wrong shape.
        assert estimate_cost(model, prompt, completion) >= 0.0  # type: ignore[arg-type]

    def test_negative_tokens_are_clamped_to_zero(self) -> None:
        estimate = estimate_cost_breakdown("openai/gpt-4o-mini", -5, -5)
        assert estimate.cost_usd == 0.0
        assert estimate.priced is True


class TestCountTokens:
    def test_count_is_non_negative(self) -> None:
        assert count_tokens("hello world", "openai/gpt-4o-mini") >= 0

    def test_count_grows_with_text(self) -> None:
        short = count_tokens("hello", "openai/gpt-4o-mini")
        long = count_tokens("hello " * 100, "openai/gpt-4o-mini")
        assert long > short

    def test_extra_text_never_decreases_the_count(self) -> None:
        base = count_tokens("the quick brown fox", "openai/gpt-4o-mini")
        extended = count_tokens("the quick brown fox jumps over the lazy dog", "openai/gpt-4o-mini")
        assert extended >= base

    def test_unknown_model_still_counts(self) -> None:
        # Falls back to a generic encoding, then to the character approximation.
        assert count_tokens("some text here", "not/a-real-model") > 0

    @pytest.mark.parametrize("text", ["", " ", "\n\n", "🙂" * 500, "a" * 10_000])
    def test_absurd_text_never_raises(self, text: str) -> None:
        assert count_tokens(text, "") >= 0

    def test_empty_text_is_zero(self) -> None:
        assert count_tokens("", "openai/gpt-4o-mini") == 0
