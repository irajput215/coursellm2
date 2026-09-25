"""The LiteLLM-backed gateway, the in-process echo gateway, and the protocol.

``LiteLLMGateway.complete`` is the reference path for a model call. Its contract:

* **Retryable failures are retried, others are not.** Rate limits, timeouts,
  connection errors and 5xx responses get bounded exponential backoff;
  authentication and malformed-request errors move straight to the next model,
  because retrying them only adds latency to a guaranteed failure.
* **Fallback is ordered and honest.** Each model in the chain is tried in turn,
  and the response records whether a fallback answered.
* **Structured output is validated or refused.** A response that does not match
  the caller's schema is retried once with a repair instruction; if it still
  fails, an :class:`~coursellm.core.errors.UpstreamError` is raised naming the
  schema. Partially-valid data is never returned to be misinterpreted
  downstream.
* **Accounting never fails a request.** A usage row is attempted for every
  provider attempt, success or failure, but a persistence error is logged and
  swallowed. Spend tracking is important; it is not more important than the
  student's answer.
* **Prompts never reach a log or the usage table.** Events carry the task,
  purpose label, model and token counts only.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError, UpstreamError, ValidationError
from coursellm.core.logging import get_logger
from coursellm.db.models.usage import LLMUsage
from coursellm.db.tenancy import set_tenant_guc
from coursellm.llm.cost import CostEstimate, count_tokens, estimate_cost_breakdown
from coursellm.llm.routing import fallback_chain, provider_of
from coursellm.llm.types import (
    LLMRequest,
    LLMResponse,
    UsageRecord,
    current_llm_scope,
)
from coursellm.observability import metrics, tracing
from coursellm.observability.attributes import (
    COURSELLM_LLM_CACHE_HIT,
    COURSELLM_LLM_COST_USD,
    COURSELLM_LLM_FALLBACK_USED,
    COURSELLM_LLM_PROMPT_VERSION,
    COURSELLM_LLM_PROVIDER,
    COURSELLM_LLM_RETRY_COUNT,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    SPAN_LLM_CALL,
)


@contextmanager
def _pristine_environment() -> Any:
    """Run a block without letting it rewrite ``os.environ``.

    ``litellm`` calls ``load_dotenv()`` at import time whenever
    ``LITELLM_MODE`` is not ``PROD`` (its default), so importing it makes a
    developer's ``.env`` visible to the entire process. That is a
    supply-chain-adjacent smell and it had a concrete symptom: an integration
    test that constructs ``Settings(_env_file=None)`` still saw ``DEBUG=true``
    and failed on machines with a ``.env`` while passing in CI. Snapshotting and
    restoring the environment around the import contains it; the credentials the
    gateway actually needs are published explicitly from :class:`Settings` in
    ``LiteLLMGateway.__init__``.
    """
    before = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)


# Isolated because of ``load_dotenv``; see ``_pristine_environment``.
with _pristine_environment():
    import litellm
    from litellm import exceptions as litellm_exceptions


logger = get_logger(__name__)

# An injectable sleep, so unit tests exercise the backoff decisions without
# actually waiting. Production always uses ``asyncio.sleep``.
SleepFn = Callable[[float], Awaitable[None]]
SessionFactory = Callable[[], Any]

# Status codes that indicate a transient condition. Matching on the code rather
# than on LiteLLM's class list means a new provider-specific exception subclass
# is classified correctly without a code change.
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# Belt and braces for exceptions that carry no usable status code. The generic
# ``APIError`` is deliberately absent: authentication and bad-request errors are
# subclasses of it, and retrying those is pure latency.
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    litellm_exceptions.RateLimitError,
    litellm_exceptions.Timeout,
    litellm_exceptions.APIConnectionError,
    litellm_exceptions.InternalServerError,
    litellm_exceptions.ServiceUnavailableError,
    litellm_exceptions.BadGatewayError,
)

_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0
_REPAIR_INSTRUCTION = (
    "Your previous response could not be parsed as the required JSON object. "
    "The validation error was: {error}. "
    "Reply with only a valid JSON object that matches the schema, and no prose."
)
_STRUCTURED_OUTPUT_ERROR_TYPE = "StructuredOutputError"


class LLMGateway(Protocol):
    """The interface every model gateway implements.

    Call sites depend on this, never on LiteLLM directly, so a scripted test
    gateway or a standalone gateway deployment is a substitution rather than a
    rewrite.
    """

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    def stream(self, request: LLMRequest) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class _Completion:
    """Normalised fields extracted from one provider response."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str | None
    latency_ms: float


class _StructuredOutputError(Exception):
    """Internal signal that a structured response failed after one repair."""


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _int_or_zero(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_retryable(exc: BaseException) -> bool:
    """Whether another attempt at the same model could plausibly succeed."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS_CODES:
        return True
    return isinstance(exc, _RETRYABLE_EXCEPTIONS)


def _backoff_seconds(retry_index: int) -> float:
    """Bounded exponential backoff with a small jitter.

    Jitter matters when many requests fail at once: without it, every client
    retries on the same tick and the provider is hit by a synchronised wave.
    ``secrets`` rather than ``random`` keeps the linter's non-cryptographic
    random check from firing on a value that is not security-sensitive but also
    is not worth an exception.
    """
    exponential: float = min(_BACKOFF_BASE_SECONDS * (2**retry_index), _BACKOFF_MAX_SECONDS)
    jitter: float = secrets.randbelow(1000) / 10_000  # 0.000-0.0999s
    return exponential + jitter


def _completion_from_response(response: Any, model: str) -> _Completion:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise UpstreamError(f"The model {model!r} returned no choices.")
    choice = choices[0]
    message = getattr(choice, "message", None)
    text = ""
    if message is not None:
        text = getattr(message, "content", "") or ""
    usage = getattr(response, "usage", None)
    prompt_tokens = _int_or_zero(getattr(usage, "prompt_tokens", 0))
    completion_tokens = _int_or_zero(getattr(usage, "completion_tokens", 0))
    total_tokens = _int_or_zero(getattr(usage, "total_tokens", 0))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return _Completion(
        text=text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        finish_reason=getattr(choice, "finish_reason", None),
        latency_ms=0.0,
    )


def _chunk_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return getattr(delta, "content", "") or ""


#: Provider credential environment variables litellm reads at call time. The
#: application's source of truth is :class:`Settings`; this is the only place a
#: value is copied into the process environment, and only these four names.
_PROVIDER_KEY_VARS = (
    ("OPENAI_API_KEY", "openai_api_key"),
    ("ANTHROPIC_API_KEY", "anthropic_api_key"),
    ("GEMINI_API_KEY", "gemini_api_key"),
    ("GROQ_API_KEY", "groq_api_key"),
)


def publish_provider_keys(settings: Settings) -> None:
    """Expose the configured provider credentials to litellm.

    ``litellm`` resolves API keys from ``os.environ`` when it is not given an
    explicit ``api_key``. The gateway publishes exactly the four keys it knows
    about from :class:`Settings`, instead of relying on litellm's import-time
    ``load_dotenv()`` — which would also leak unrelated variables such as
    ``DEBUG`` into a process that deliberately bypassed its ``.env``.
    """
    for variable, attribute in _PROVIDER_KEY_VARS:
        value = getattr(settings, attribute, "")
        if value:
            os.environ[variable] = value


class LiteLLMGateway:
    """The production gateway. All provider access goes through LiteLLM."""

    def __init__(
        self,
        settings: Settings,
        session_factory: SessionFactory | None = None,
        *,
        sleep: SleepFn | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._sleep: SleepFn = sleep or asyncio.sleep
        publish_provider_keys(settings)

    # -- public API ------------------------------------------------------
    async def complete(self, request: LLMRequest) -> LLMResponse:
        """One logical model call, wrapped in an LLM span.

        The span is opened here rather than in ``_invoke`` so that a subclass
        which replaces the provider call (the scripted gateways used by tests)
        still produces a span carrying the same attributes as production.
        """
        primary = fallback_chain(self._settings, request.task)[0]
        with tracing.span(
            SPAN_LLM_CALL,
            **{
                GEN_AI_OPERATION_NAME: "chat",
                GEN_AI_REQUEST_MODEL: primary,
                COURSELLM_LLM_PROMPT_VERSION: request.purpose,
                COURSELLM_LLM_RETRY_COUNT: 0,
            },
        ) as record:
            response = await self._complete_impl(request)
            record.set_attributes(
                {
                    GEN_AI_SYSTEM: response.provider,
                    GEN_AI_REQUEST_MODEL: response.model,
                    GEN_AI_RESPONSE_MODEL: response.model,
                    COURSELLM_LLM_PROVIDER: response.provider,
                    GEN_AI_USAGE_INPUT_TOKENS: response.prompt_tokens,
                    GEN_AI_USAGE_OUTPUT_TOKENS: response.completion_tokens,
                    GEN_AI_RESPONSE_FINISH_REASONS: (
                        [response.finish_reason] if response.finish_reason else []
                    ),
                    COURSELLM_LLM_COST_USD: response.cost_usd,
                    COURSELLM_LLM_FALLBACK_USED: response.fallback_used,
                    COURSELLM_LLM_CACHE_HIT: response.cached,
                }
            )
            return response

    async def _complete_impl(self, request: LLMRequest) -> LLMResponse:
        if not self._settings.llm_enabled:
            raise ServiceUnavailableError(
                "The language model gateway is disabled; callers must degrade."
            )

        chain = fallback_chain(self._settings, request.task)
        primary = chain[0]
        attempt = 0
        last_error: BaseException | None = None

        for model in chain:
            provider = provider_of(model)
            used_fallback = model != primary
            retries = self._settings.llm_max_retries
            for retry_index in range(retries + 1):
                attempt += 1
                try:
                    completion = await self._invoke(
                        model,
                        request,
                        attempt=attempt,
                        provider=provider,
                        used_fallback=used_fallback,
                    )
                    if request.response_model is not None:
                        parsed, completion, attempt = await self._validated(
                            request, model, provider, used_fallback, attempt, completion
                        )
                    else:
                        parsed = None
                    return await self._succeeded(
                        request, model, provider, used_fallback, attempt, completion, parsed
                    )
                except _StructuredOutputError as exc:
                    schema_name = (
                        request.response_model.__name__
                        if request.response_model is not None
                        else "unknown"
                    )
                    raise UpstreamError(
                        f"The model response did not satisfy the {schema_name!r} schema "
                        f"after one repair attempt; no data was returned."
                    ) from exc
                except Exception as exc:  # classified below and re-raised at the end
                    last_error = exc
                    if not _is_retryable(exc):
                        break
                    if retry_index < retries:
                        await self._sleep(_backoff_seconds(retry_index))
                        continue
                    break

        raise UpstreamError(
            f"Every configured model failed for task {request.task.value!r}."
        ) from last_error

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """Streaming LLM call, wrapped in one span for the whole stream."""
        primary = fallback_chain(self._settings, request.task)[0]
        with tracing.span(
            SPAN_LLM_CALL,
            **{
                GEN_AI_OPERATION_NAME: "chat",
                GEN_AI_REQUEST_MODEL: primary,
                COURSELLM_LLM_PROMPT_VERSION: request.purpose,
                COURSELLM_LLM_RETRY_COUNT: 0,
            },
        ) as _record:
            async for token in self._stream_impl(request):
                yield token

    async def _stream_impl(self, request: LLMRequest) -> AsyncIterator[str]:
        if not self._settings.llm_enabled:
            raise ServiceUnavailableError(
                "The language model gateway is disabled; callers must degrade."
            )
        if request.response_model is not None:
            raise ValidationError(
                "Streaming is not supported for structured requests; use complete()."
            )

        chain = fallback_chain(self._settings, request.task)
        primary = chain[0]
        last_error: BaseException | None = None

        for model in chain:
            provider = provider_of(model)
            used_fallback = model != primary
            started = time.perf_counter()
            try:
                stream = await asyncio.wait_for(
                    litellm.acompletion(
                        model=model,
                        messages=[message.model_dump() for message in request.messages],
                        temperature=request.temperature,
                        stream=True,
                    ),
                    timeout=self._settings.llm_request_timeout_seconds,
                )
            except Exception as exc:  # fallback is the handling
                last_error = exc
                await self._failed(
                    request, model, provider, used_fallback, 1, _elapsed_ms(started), exc
                )
                continue

            # A fallback can only be chosen before the first token: once text has
            # been yielded, switching models mid-stream would produce a spliced
            # answer. See ADR-0007.
            emitted = False
            try:
                async for chunk in stream:
                    text = _chunk_text(chunk)
                    if text:
                        emitted = True
                        yield text
            except Exception as exc:  # decided by ``emitted`` below
                last_error = exc
                await self._failed(
                    request, model, provider, used_fallback, 1, _elapsed_ms(started), exc
                )
                if emitted:
                    raise UpstreamError(
                        "The model stream failed after the first token; a fallback "
                        "cannot resume mid-stream."
                    ) from exc
                continue

            # Streaming responses do not reliably carry a usage block, so the
            # record is honest about what is unknown rather than inventing zeros
            # that look like a priced call.
            completion = _Completion(
                text="",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                finish_reason=None,
                latency_ms=_elapsed_ms(started),
            )
            await self._succeeded(
                request, model, provider, used_fallback, 1, completion, parsed=None
            )
            return

        raise UpstreamError(
            f"Every configured model failed for task {request.task.value!r}."
        ) from last_error

    # -- provider call ---------------------------------------------------
    async def _invoke(
        self,
        model: str,
        request: LLMRequest,
        *,
        attempt: int,
        provider: str,
        used_fallback: bool,
        repair: str | None = None,
    ) -> _Completion:
        """Make one provider call, recording its outcome.

        A provider failure is persisted here so that every attempt has exactly
        one usage row: successes are persisted by the caller once validation has
        also passed, failures are persisted before the exception propagates.
        """
        messages = [message.model_dump() for message in request.messages]
        if repair is not None:
            messages.append({"role": "user", "content": repair})
        max_tokens = (
            request.max_tokens
            if request.max_tokens is not None
            else self._settings.llm_max_output_tokens
        )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": request.temperature,
            "max_tokens": max_tokens,
        }
        if request.response_model is not None:
            kwargs["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**kwargs),
                timeout=self._settings.llm_request_timeout_seconds,
            )
            completion = _completion_from_response(response, model)
        except Exception as exc:  # recorded, then re-raised
            await self._failed(
                request, model, provider, used_fallback, attempt, _elapsed_ms(started), exc
            )
            raise
        return _Completion(
            text=completion.text,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            finish_reason=completion.finish_reason,
            latency_ms=_elapsed_ms(started),
        )

    async def _validated(
        self,
        request: LLMRequest,
        model: str,
        provider: str,
        used_fallback: bool,
        attempt: int,
        completion: _Completion,
    ) -> tuple[Any, _Completion, int]:
        """Validate structured output, with a single repair attempt.

        Raises :class:`_StructuredOutputError` when the repaired response still
        does not match. Each of the two calls already produced its own usage row.
        """
        schema = request.response_model
        if schema is None:  # pragma: no cover - guarded by the caller
            return None, completion, attempt
        try:
            return schema.model_validate_json(completion.text), completion, attempt
        except PydanticValidationError as first_error:
            await self._failed_record(
                request,
                model,
                provider,
                used_fallback,
                attempt,
                completion.latency_ms,
                _STRUCTURED_OUTPUT_ERROR_TYPE,
            )
            repair_attempt = attempt + 1
            repaired = await self._invoke(
                model,
                request,
                attempt=repair_attempt,
                provider=provider,
                used_fallback=used_fallback,
                repair=_REPAIR_INSTRUCTION.format(error=first_error),
            )
            try:
                parsed = schema.model_validate_json(repaired.text)
            except PydanticValidationError as second_error:
                await self._failed_record(
                    request,
                    model,
                    provider,
                    used_fallback,
                    repair_attempt,
                    repaired.latency_ms,
                    _STRUCTURED_OUTPUT_ERROR_TYPE,
                )
                raise _StructuredOutputError(str(second_error)) from second_error
            return parsed, repaired, repair_attempt

    # -- accounting ------------------------------------------------------
    def _record(
        self,
        request: LLMRequest,
        model: str,
        provider: str,
        used_fallback: bool,
        attempt: int,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: float,
        error_type: str | None,
        estimate: CostEstimate | None = None,
    ) -> UsageRecord | None:
        scope = current_llm_scope()
        if scope is None:
            return None
        if estimate is None:
            estimate = estimate_cost_breakdown(model, prompt_tokens, completion_tokens)
        return UsageRecord(
            tenant_id=scope.tenant_id,
            task=request.task,
            purpose=request.purpose,
            model=model[:120],
            provider=provider[:40],
            used_fallback=used_fallback,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=estimate.cost_usd,
            priced=estimate.priced,
            latency_ms=max(round(latency_ms), 0),
            attempt=attempt,
            user_id=scope.user_id,
            conversation_id=scope.conversation_id,
            request_id=scope.request_id,
            error_type=error_type,
        )

    async def _failed(
        self,
        request: LLMRequest,
        model: str,
        provider: str,
        used_fallback: bool,
        attempt: int,
        latency_ms: float,
        error: BaseException,
    ) -> None:
        await self._failed_record(
            request,
            model,
            provider,
            used_fallback,
            attempt,
            latency_ms,
            type(error).__name__,
        )

    async def _failed_record(
        self,
        request: LLMRequest,
        model: str,
        provider: str,
        used_fallback: bool,
        attempt: int,
        latency_ms: float,
        error_type: str,
    ) -> None:
        logger.warning(
            "llm_request_failed",
            task=request.task.value,
            purpose=request.purpose,
            model=model,
            provider=provider,
            attempt=attempt,
            error_type=error_type,
        )
        metrics.get_registry().increment(
            "coursellm.errors.total", route="llm", error_class=error_type
        )
        if attempt > 1:
            metrics.get_registry().increment(
                "coursellm.llm.retries.total", model=model, reason="provider_error"
            )
        record = self._record(
            request,
            model,
            provider,
            used_fallback,
            attempt,
            0,
            0,
            0,
            latency_ms,
            error_type,
        )
        await self._persist(record)

    async def _succeeded(
        self,
        request: LLMRequest,
        model: str,
        provider: str,
        used_fallback: bool,
        attempt: int,
        completion: _Completion,
        parsed: Any,
    ) -> LLMResponse:
        estimate = estimate_cost_breakdown(
            model, completion.prompt_tokens, completion.completion_tokens
        )
        metrics.record_llm_call(
            model=model,
            provider=provider,
            operation="chat",
            duration_ms=completion.latency_ms,
            input_tokens=completion.prompt_tokens,
            output_tokens=completion.completion_tokens,
            cost_usd=estimate.cost_usd,
            fallback_used=used_fallback,
            primary_model=fallback_chain(self._settings, request.task)[0],
            retry_count=max(attempt - 1, 0),
        )
        logger.info(
            "llm_request_completed",
            task=request.task.value,
            purpose=request.purpose,
            model=model,
            provider=provider,
            attempt=attempt,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            latency_ms=round(completion.latency_ms, 2),
            used_fallback=used_fallback,
        )
        record = self._record(
            request,
            model,
            provider,
            used_fallback,
            attempt,
            completion.prompt_tokens,
            completion.completion_tokens,
            completion.total_tokens,
            completion.latency_ms,
            None,
            estimate,
        )
        await self._persist(record)
        return LLMResponse(
            text=completion.text,
            parsed=parsed,
            model=model,
            provider=provider,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.total_tokens,
            cost_usd=estimate.cost_usd,
            latency_ms=completion.latency_ms,
            cached=False,
            fallback_used=used_fallback,
            finish_reason=completion.finish_reason,
        )

    async def _persist(self, record: UsageRecord | None) -> None:
        """Write one usage row, swallowing any failure.

        Persistence is best-effort by design: a database problem in the
        accounting path must never turn a delivered answer into an error. The
        failure is logged so the gap is visible operationally.
        """
        if record is None or self._session_factory is None:
            return
        try:
            async with self._session_factory() as session, session.begin():
                await set_tenant_guc(session, record.tenant_id)
                session.add(
                    LLMUsage(
                        tenant_id=record.tenant_id,
                        user_id=record.user_id,
                        conversation_id=record.conversation_id,
                        request_id=record.request_id,
                        task=record.task,
                        purpose=record.purpose,
                        model=record.model,
                        provider=record.provider,
                        used_fallback=record.used_fallback,
                        prompt_tokens=record.prompt_tokens,
                        completion_tokens=record.completion_tokens,
                        total_tokens=record.total_tokens,
                        cost_usd=Decimal(str(round(record.cost_usd, 6))),
                        priced=record.priced,
                        latency_ms=record.latency_ms,
                        attempt=record.attempt,
                        error_type=record.error_type,
                    )
                )
        except Exception:  # accounting must never fail the request
            logger.exception(
                "llm_usage_persist_failed",
                model=record.model,
                task=record.task.value,
            )


class EchoGateway:
    """A deterministic, dependency-free gateway.

    Used for tests and for ``LLM_ENABLED=false`` local runs. It performs no I/O:
    the body of the last message is echoed, or, for a structured request, a
    default-constructed instance of the schema is returned.

    That last behaviour has a deliberate limit. If the schema has required
    fields with no defaults, the gateway raises
    :class:`~coursellm.core.errors.ServiceUnavailableError` instead of inventing
    values — fabricated data that looks valid is worse than an explicit failure.
    Tests that need a specific structured payload must inject a scripted
    gateway; this one exists so that the application can boot and respond
    without a provider, not so that structured paths can be tested against it.
    """

    model_name = "echo"
    provider = "local"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        parsed = None
        if request.response_model is not None:
            parsed = self._construct(request)
            text = parsed.model_dump_json()
        else:
            text = request.messages[-1].content if request.messages else ""

        prompt_tokens = sum(
            count_tokens(message.content, self.model_name) for message in request.messages
        )
        completion_tokens = count_tokens(text, self.model_name)
        return LLMResponse(
            text=text,
            parsed=parsed,
            model=self.model_name,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost_usd=0.0,
            latency_ms=0.0,
            cached=False,
            fallback_used=False,
            finish_reason="stop",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        response = await self.complete(request)
        for line in response.text.splitlines(keepends=True) or [""]:
            yield line

    def _construct(self, request: LLMRequest) -> Any:
        schema = request.response_model
        if schema is None:  # pragma: no cover - guarded by the caller
            return None
        try:
            return schema()
        except PydanticValidationError as exc:
            raise ServiceUnavailableError(
                f"The echo gateway cannot construct {schema.__name__!r} without "
                f"fabricating data; inject a scripted gateway for structured paths."
            ) from exc


# The cache is intentionally a single slot: settings are process-wide, so a
# second call with different settings is a wiring error rather than a use case.
_gateway_cache: LLMGateway | None = None


def get_gateway(settings: Settings, session_factory: SessionFactory | None = None) -> LLMGateway:
    """Return the process-wide gateway, choosing the implementation by config.

    ``EchoGateway`` when ``llm_enabled`` is false so that local development and
    the test suite need no provider key; otherwise the LiteLLM gateway.
    """
    global _gateway_cache
    if _gateway_cache is None:
        if settings.llm_enabled:
            _gateway_cache = LiteLLMGateway(settings, session_factory)
        else:
            _gateway_cache = EchoGateway()
    return _gateway_cache


def reset_gateway_cache() -> None:
    """Drop the cached gateway. For tests and for a configuration reload."""
    global _gateway_cache
    _gateway_cache = None
