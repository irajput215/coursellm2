"""Task routing and fallback-chain tests.

Routing is pure, so these are exhaustive rather than representative: every task
is pinned to its model, and the two edge cases that would otherwise fail at
request time (blank configuration, a fallback equal to the primary) are pinned
too.
"""

from __future__ import annotations

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import ValidationError
from coursellm.llm.routing import fallback_chain, provider_of, resolve_model
from coursellm.llm.types import ModelTask

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "primary_model": "openai/gpt-4o-mini",
        "fallback_model": "anthropic/claude-3-5-haiku-latest",
        "fast_model": "openai/gpt-4o-mini-fast",
        "reasoning_model": "openai/gpt-4o",
        "llm_routing_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class TestResolveModel:
    @pytest.mark.parametrize(
        ("task", "expected"),
        [
            (ModelTask.CLASSIFICATION, "openai/gpt-4o-mini-fast"),
            (ModelTask.EXTRACTION, "openai/gpt-4o-mini-fast"),
            (ModelTask.TUTORING, "openai/gpt-4o-mini"),
            (ModelTask.REASONING, "openai/gpt-4o"),
        ],
    )
    def test_each_task_maps_to_its_configured_model(self, task: ModelTask, expected: str) -> None:
        assert resolve_model(_settings(), task) == expected

    def test_routing_disabled_collapses_every_task_to_the_primary(self) -> None:
        settings = _settings(
            llm_routing_enabled=False,
            primary_model="openai/gpt-4o",
            fast_model="openai/gpt-4o-mini",
            reasoning_model="openai/o1",
        )
        assert {resolve_model(settings, task) for task in ModelTask} == {"openai/gpt-4o"}

    def test_whitespace_is_normalised(self) -> None:
        settings = _settings(primary_model="  openai/gpt-4o-mini  ")
        assert resolve_model(settings, ModelTask.TUTORING) == "openai/gpt-4o-mini"

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_configuration_raises_a_clear_error(self, blank: str) -> None:
        settings = _settings(llm_routing_enabled=False, primary_model=blank)
        with pytest.raises(ValidationError) as caught:
            resolve_model(settings, ModelTask.TUTORING)
        assert "No model is configured" in str(caught.value)

    def test_blank_task_specific_model_raises(self) -> None:
        settings = _settings(fast_model="")
        with pytest.raises(ValidationError):
            resolve_model(settings, ModelTask.CLASSIFICATION)


class TestFallbackChain:
    def test_chain_contains_primary_then_fallback(self) -> None:
        settings = _settings()
        assert fallback_chain(settings, ModelTask.TUTORING) == [
            "openai/gpt-4o-mini",
            "anthropic/claude-3-5-haiku-latest",
        ]

    def test_chain_is_de_duplicated(self) -> None:
        settings = _settings(fallback_model="openai/gpt-4o-mini")
        assert fallback_chain(settings, ModelTask.TUTORING) == ["openai/gpt-4o-mini"]

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_fallback_is_removed(self, blank: str) -> None:
        settings = _settings(fallback_model=blank)
        assert fallback_chain(settings, ModelTask.TUTORING) == ["openai/gpt-4o-mini"]

    def test_chain_follows_task_routing(self) -> None:
        settings = _settings()
        assert fallback_chain(settings, ModelTask.REASONING)[0] == "openai/gpt-4o"


class TestProviderOf:
    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("openai/gpt-4o-mini", "openai"),
            ("anthropic/claude-3-5-haiku-latest", "anthropic"),
            ("gemini/gemini-1.5-flash", "gemini"),
            ("gpt-4o-mini", "unknown"),
            ("", "unknown"),
            ("   ", "unknown"),
            ("/leading-slash", "unknown"),
        ],
    )
    def test_provider_prefixes(self, model: str, expected: str) -> None:
        assert provider_of(model) == expected

    def test_multi_slash_reports_only_the_first_segment(self) -> None:
        assert provider_of("openai/gpt-4o/mini") == "openai"

    def test_padding_does_not_create_a_provider(self) -> None:
        assert provider_of("  openai/gpt-4o-mini  ") == "openai"
