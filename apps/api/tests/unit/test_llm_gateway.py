"""Gateway behaviour tests.

No network call is made: ``litellm.acompletion`` is replaced with a scripted
double, so retry, fallback, timeout, structured-output and accounting decisions
are tested deterministically.

The backoff sleep is injected so a retry test asserts *that* a retry happened
without waiting for it.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from pydantic import BaseModel

from coursellm.core.config import Environment, Settings
from coursellm.core.errors import ServiceUnavailableError, UpstreamError
from coursellm.llm import gateway as gateway_module
from coursellm.llm.gateway import (
    EchoGateway,
    LiteLLMGateway,
    get_gateway,
    reset_gateway_cache,
)
from coursellm.llm.types import LLMRequest, LLMScope, ModelTask, bind_llm_scope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "environment": Environment.LOCAL,
        "debug": True,
        "secret_key": "unit-" + "not-a-real-key-" * 5,
        "llm_enabled": True,
        "llm_routing_enabled": True,
        "primary_model": "openai/gpt-4o-mini",
        "fallback_model": "anthropic/claude-3-5-haiku-latest",
        "fast_model": "openai/gpt-4o-mini",
        "reasoning_model": "openai/gpt-4o",
        "llm_max_retries": 2,
        "llm_request_timeout_seconds": 5,
        "llm_max_output_tokens": 512,
        "database_url": "postgresql+asyncpg://localhost:5432/coursellm_test",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _request(**overrides: object) -> LLMRequest:
    base: dict[str, object] = {
        "task": ModelTask.TUTORING,
        "messages": [{"role": "user", "content": "What is a derivative?"}],
        "purpose": "tutor.answer",
    }
    base.update(overrides)
    return LLMRequest(**base)  # type: ignore[arg-type]


def _response(
    *, content: str = "a derivative is a rate of change", prompt: int = 10, completion: int = 5
) -> Any:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )


async def _no_sleep(_seconds: float) -> None:
    return None


class _Script:
    """Returns queued outcomes in order, recording every call's kwargs."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.outcomes:
            msg = "scripted acompletion received an unexpected extra call"
            raise AssertionError(msg)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _AlwaysFails:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise self.error


def _rate_limit() -> litellm.RateLimitError:
    return litellm.RateLimitError(message="slow down", llm_provider="openai", model="gpt-4o-mini")


def _auth_error() -> litellm.AuthenticationError:
    return litellm.AuthenticationError(
        message="bad key", llm_provider="openai", model="gpt-4o-mini"
    )


class _FakeSession:
    def __init__(self, sink: list[Any], *, fail_on_add: bool) -> None:
        self._sink = sink
        self._fail_on_add = fail_on_add

    async def execute(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def add(self, entity: Any) -> None:
        if self._fail_on_add:
            msg = "database unavailable"
            raise RuntimeError(msg)
        self._sink.append(entity)

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    def begin(self) -> _FakeSession:
        return self


class _FakeSessionFactory:
    def __init__(self, *, fail: bool = False) -> None:
        self.added: list[Any] = []
        self._fail = fail

    def __call__(self) -> _FakeSession:
        return _FakeSession(self.added, fail_on_add=self._fail)


class _CapturingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _record(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))

    debug = _record
    info = _record
    warning = _record
    error = _record
    exception = _record


class _Verdict(BaseModel):
    label: str
    confidence: float


class _AllDefaults(BaseModel):
    ok: bool = True


class _NeedsAField(BaseModel):
    value: str


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------
class TestComplete:
    async def test_success_path_returns_text_and_usage(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response(content="hello there", prompt=10, completion=4))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(), sleep=_no_sleep)

        response = await gateway.complete(_request())

        assert response.text == "hello there"
        assert response.model == "openai/gpt-4o-mini"
        assert response.provider == "openai"
        assert response.prompt_tokens == 10
        assert response.completion_tokens == 4
        assert response.total_tokens == 14
        assert response.cost_usd > 0.0
        assert response.fallback_used is False
        assert response.finish_reason == "stop"

    async def test_retryable_error_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_rate_limit(), _response(content="second time lucky"))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(llm_max_retries=2), sleep=_no_sleep)

        response = await gateway.complete(_request())

        assert response.text == "second time lucky"
        assert len(script.calls) == 2
        assert response.fallback_used is False

    async def test_non_retryable_error_moves_straight_to_the_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_auth_error(), _response(content="fallback answered"))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(llm_max_retries=2), sleep=_no_sleep)

        response = await gateway.complete(_request())

        assert response.text == "fallback answered"
        assert response.model == "anthropic/claude-3-5-haiku-latest"
        assert response.fallback_used is True
        # One auth failure, then the fallback: no retry of a guaranteed failure.
        assert len(script.calls) == 2

    async def test_exhausted_chain_raises_upstream_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing = _AlwaysFails(_rate_limit())
        monkeypatch.setattr(litellm, "acompletion", failing)
        gateway = LiteLLMGateway(_settings(llm_max_retries=1), sleep=_no_sleep)

        with pytest.raises(UpstreamError):
            await gateway.complete(_request())

        # Two attempts per model, two models in the chain.
        assert len(failing.calls) == 4

    async def test_llm_disabled_raises_service_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response())
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(llm_enabled=False), sleep=_no_sleep)

        with pytest.raises(ServiceUnavailableError):
            await gateway.complete(_request())

        assert script.calls == []

    async def test_timeout_raises_upstream_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def slow(**_kwargs: Any) -> Any:
            await asyncio.sleep(10)
            return _response()

        monkeypatch.setattr(litellm, "acompletion", slow)
        gateway = LiteLLMGateway(
            _settings(
                fallback_model="",
                llm_max_retries=0,
                llm_request_timeout_seconds=1,
            ),
            sleep=_no_sleep,
        )

        with pytest.raises(UpstreamError):
            await gateway.complete(_request())


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------
class TestStructuredOutput:
    async def test_valid_structured_response_is_parsed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response(content='{"label": "ok", "confidence": 0.9}'))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(), sleep=_no_sleep)

        response = await gateway.complete(_request(response_model=_Verdict))

        assert isinstance(response.parsed, _Verdict)
        assert response.parsed.label == "ok"
        # The provider was asked for JSON, not for prose.
        assert script.calls[0]["response_format"] == {"type": "json_object"}

    async def test_invalid_structured_response_repairs_once_then_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response(content="not json"), _response(content="still not json"))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(fallback_model="", llm_max_retries=2), sleep=_no_sleep)

        with pytest.raises(UpstreamError) as caught:
            await gateway.complete(_request(response_model=_Verdict))

        assert "_Verdict" in str(caught.value)
        # Exactly one repair attempt, and retries do not multiply a schema error.
        assert len(script.calls) == 2
        repair_messages = script.calls[1]["messages"]
        assert "JSON" in repair_messages[-1]["content"]

    async def test_invalid_structured_response_repaired_successfully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(
            _response(content="{broken"),
            _response(content='{"label": "repaired", "confidence": 0.5}'),
        )
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(fallback_model=""), sleep=_no_sleep)

        response = await gateway.complete(_request(response_model=_Verdict))

        assert isinstance(response.parsed, _Verdict)
        assert response.parsed.label == "repaired"


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------
class TestAccounting:
    async def test_success_writes_one_usage_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        script = _Script(_response(content="ok", prompt=11, completion=3))
        monkeypatch.setattr(litellm, "acompletion", script)
        factory = _FakeSessionFactory()
        gateway = LiteLLMGateway(_settings(), factory, sleep=_no_sleep)
        tenant_id = uuid.uuid4()

        with bind_llm_scope(LLMScope(tenant_id=tenant_id)):
            await gateway.complete(_request())

        assert len(factory.added) == 1
        row = factory.added[0]
        assert row.tenant_id == tenant_id
        assert row.model == "openai/gpt-4o-mini"
        assert row.prompt_tokens == 11
        assert row.completion_tokens == 3
        assert row.error_type is None
        assert row.used_fallback is False
        assert row.attempt == 1
        assert row.priced is True
        assert row.cost_usd > 0

    async def test_failure_writes_a_row_with_an_error_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failing = _AlwaysFails(_rate_limit())
        monkeypatch.setattr(litellm, "acompletion", failing)
        factory = _FakeSessionFactory()
        gateway = LiteLLMGateway(
            _settings(fallback_model="", llm_max_retries=0), factory, sleep=_no_sleep
        )

        with pytest.raises(UpstreamError), bind_llm_scope(LLMScope(tenant_id=uuid.uuid4())):
            await gateway.complete(_request())

        assert len(factory.added) == 1
        row = factory.added[0]
        assert row.error_type == "RateLimitError"
        assert row.prompt_tokens == 0
        assert row.completion_tokens == 0

    async def test_persistence_failure_does_not_fail_the_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response(content="still answered"))
        monkeypatch.setattr(litellm, "acompletion", script)
        gateway = LiteLLMGateway(_settings(), _FakeSessionFactory(fail=True), sleep=_no_sleep)

        with bind_llm_scope(LLMScope(tenant_id=uuid.uuid4())):
            response = await gateway.complete(_request())

        assert response.text == "still answered"

    async def test_no_ambient_tenant_skips_persistence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = _Script(_response(content="ok"))
        monkeypatch.setattr(litellm, "acompletion", script)
        factory = _FakeSessionFactory()
        gateway = LiteLLMGateway(_settings(), factory, sleep=_no_sleep)

        # No scope bound: there is no tenant to attribute the row to, and the
        # database would refuse an unattributed insert anyway.
        response = await gateway.complete(_request())

        assert response.text == "ok"
        assert factory.added == []


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------
class TestNoPromptContentIsLogged:
    async def test_prompt_text_never_appears_in_a_log_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret_prompt = "MIDTERM-ANSWER-KEY-do-not-log"
        script = _Script(_response(content="a response"))
        monkeypatch.setattr(litellm, "acompletion", script)
        capture = _CapturingLogger()
        monkeypatch.setattr(gateway_module, "logger", capture)
        gateway = LiteLLMGateway(_settings(), sleep=_no_sleep)

        await gateway.complete(_request(messages=[{"role": "user", "content": secret_prompt}]))

        assert any(event == "llm_request_completed" for event, _ in capture.events)
        assert secret_prompt not in repr(capture.events)

    async def test_prompt_text_never_appears_on_the_failure_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret_prompt = "ANOTHER-SECRET-PROMPT"
        failing = _AlwaysFails(_rate_limit())
        monkeypatch.setattr(litellm, "acompletion", failing)
        capture = _CapturingLogger()
        monkeypatch.setattr(gateway_module, "logger", capture)
        gateway = LiteLLMGateway(_settings(fallback_model="", llm_max_retries=0), sleep=_no_sleep)

        with pytest.raises(UpstreamError):
            await gateway.complete(_request(messages=[{"role": "user", "content": secret_prompt}]))

        assert any(event == "llm_request_failed" for event, _ in capture.events)
        assert secret_prompt not in repr(capture.events)


# ---------------------------------------------------------------------------
# EchoGateway and get_gateway
# ---------------------------------------------------------------------------
class TestEchoGateway:
    async def test_text_is_deterministic(self) -> None:
        gateway = EchoGateway()
        first = await gateway.complete(_request())
        second = await gateway.complete(_request())
        assert first.text == second.text
        assert first.text == "What is a derivative?"

    async def test_no_cost_and_no_provider(self) -> None:
        response = await EchoGateway().complete(_request())
        assert response.cost_usd == 0.0
        assert response.provider == "local"
        assert response.fallback_used is False

    async def test_constructible_structured_model_is_returned(self) -> None:
        response = await EchoGateway().complete(_request(response_model=_AllDefaults))
        assert isinstance(response.parsed, _AllDefaults)

    async def test_unsatisfiable_structured_model_raises(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            await EchoGateway().complete(_request(response_model=_NeedsAField))

    async def test_stream_yields_text(self) -> None:
        chunks = [chunk async for chunk in EchoGateway().stream(_request())]
        assert "".join(chunks) == "What is a derivative?"


class TestGetGateway:
    def test_disabled_settings_select_the_echo_gateway(self) -> None:
        reset_gateway_cache()
        try:
            assert isinstance(get_gateway(_settings(llm_enabled=False)), EchoGateway)
        finally:
            reset_gateway_cache()

    def test_enabled_settings_select_the_litellm_gateway(self) -> None:
        reset_gateway_cache()
        try:
            assert isinstance(get_gateway(_settings(llm_enabled=True)), LiteLLMGateway)
        finally:
            reset_gateway_cache()

    def test_gateway_is_cached_per_process(self) -> None:
        reset_gateway_cache()
        try:
            settings = _settings(llm_enabled=False)
            assert get_gateway(settings) is get_gateway(settings)
        finally:
            reset_gateway_cache()
