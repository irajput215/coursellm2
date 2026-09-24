"""Guarded generation: prompt, call, verify, degrade.

The generator has three behaviours that are not obvious from the happy path, and
each of them exists because the alternative fails a student:

* **No evidence means no model call.** The refusal is composed from the versioned
  template and typed state, so it cannot be lost to a provider outage and it
  cannot be replaced by the model answering from memory.
* **An uncited answer is not a grounded answer.** Citations are verified against
  the context and unknown ids are stripped before the text is returned.
* **A provider failure degrades, it does not raise.** The extractive fallback
  quotes the top passages with their citation ids so the turn still carries
  evidence and still produces a non-empty answer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError, UpstreamError
from coursellm.core.logging import get_logger
from coursellm.llm import ChatMessage, LLMGateway, LLMRequest, LLMResponse, ModelTask
from coursellm.observability import metrics, tracing
from coursellm.observability.attributes import (
    COURSELLM_CONFIG_VERSION,
    COURSELLM_LLM_PROMPT_VERSION,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    SPAN_GENERATION,
    SPAN_OUTPUT_VALIDATION,
)
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.citations import (
    extract_citation_ids,
    is_refusal,
    strip_hallucinated,
    verify_citations,
)
from coursellm.rag.generation.context import AssembledContext, Citation

logger = get_logger(__name__)

#: No passage survived retrieval, so the answer is the refusal template.
NO_EVIDENCE = "no_evidence"
#: The model answered without citing any of the evidence it was given.
UNCITED_ANSWER = "uncited_answer"
#: The gateway could not produce an answer; an extractive answer was composed.
LLM_UNAVAILABLE = "llm_unavailable"

_ANSWER_TEMPLATE = "tutor.answer"
_REFUSAL_TEMPLATE = "tutor.refusal"
_EXTRACTIVE_TEMPLATE = "tutor.extractive"
_EXTRACTIVE_PASSAGES = 3
_EXTRACTIVE_CHARS = 400


@dataclass(frozen=True, slots=True)
class GeneratedAnswer:
    """One generated turn, after citation verification."""

    text: str
    citations: list[Citation]
    grounded: bool
    degraded: list[str]
    usage: LLMResponse | None
    model: str | None
    prompt_template_id: str


@dataclass(frozen=True, slots=True)
class TokenEvent:
    """A partial answer delta emitted while streaming."""

    text: str


@dataclass(frozen=True, slots=True)
class CompletionEvent:
    """The final streamed event, carrying the verified answer."""

    answer: GeneratedAnswer


StreamEvent = TokenEvent | CompletionEvent


class AnswerGenerator:
    """Turns a question and an :class:`AssembledContext` into a guarded answer."""

    def __init__(self, settings: Settings, gateway: LLMGateway, prompts: PromptLibrary) -> None:
        self._settings = settings
        self._gateway = gateway
        self._prompts = prompts

    async def answer(
        self,
        question: str,
        context: AssembledContext,
        *,
        course_name: str,
        history: Sequence[ChatMessage] = (),
    ) -> GeneratedAnswer:
        """Answer ``question`` from ``context``, or refuse if there is no evidence."""
        if not context.passages:
            return self._refusal(question, course_name)

        template = self._prompts.get(_ANSWER_TEMPLATE)
        request = self._request(question, context, course_name=course_name, history=history)
        with tracing.span(
            SPAN_GENERATION,
            **{
                COURSELLM_LLM_PROMPT_VERSION: template.template_id,
                COURSELLM_CONFIG_VERSION: self._settings.retrieval_config_version,
            },
        ) as record:
            try:
                response = await self._gateway.complete(request)
            except (ServiceUnavailableError, UpstreamError) as exc:
                logger.warning(
                    "tutor_generation_degraded",
                    reason=LLM_UNAVAILABLE,
                    exc_type=type(exc).__name__,
                )
                record.set_error()
                return self._extractive(question, context, course_name=course_name)
            record.set_attributes(
                {
                    GEN_AI_RESPONSE_MODEL: response.model,
                    GEN_AI_USAGE_INPUT_TOKENS: response.prompt_tokens,
                    GEN_AI_USAGE_OUTPUT_TOKENS: response.completion_tokens,
                }
            )
        return self._finalise(
            response.text,
            context,
            usage=response,
            model=response.model,
            prompt_template_id=template.template_id,
            extra_degraded=[],
        )

    async def stream(
        self,
        question: str,
        context: AssembledContext,
        *,
        course_name: str,
        history: Sequence[ChatMessage] = (),
    ) -> AsyncIterator[StreamEvent]:
        """Stream tokens, then a final :class:`CompletionEvent`.

        Post-processing is not skipped on the streaming path: the accumulated
        text is stripped, verified and classified before the completion event, so
        a client that renders partial text still receives only real citations.
        """
        if not context.passages:
            answer = self._refusal(question, course_name)
            yield TokenEvent(text=answer.text)
            yield CompletionEvent(answer=answer)
            return

        template_id = self._prompts.get(_ANSWER_TEMPLATE).template_id
        request = self._request(question, context, course_name=course_name, history=history)
        parts: list[str] = []
        degraded: list[str] = []
        try:
            async for token in self._gateway.stream(request):
                if not token:
                    continue
                parts.append(token)
                yield TokenEvent(text=token)
        except (ServiceUnavailableError, UpstreamError) as exc:
            logger.warning(
                "tutor_stream_degraded",
                reason=LLM_UNAVAILABLE,
                exc_type=type(exc).__name__,
            )
            degraded.append(LLM_UNAVAILABLE)

        text = "".join(parts)
        if not text:
            # An empty stream is a failure even if it raised nothing: returning
            # an empty answer would be a silent no-op for the student.
            if LLM_UNAVAILABLE not in degraded:
                degraded.append(LLM_UNAVAILABLE)
            text = compose_extractive(
                context, self._prompts, question=question, course_name=course_name
            )
            template_id = self._prompts.get(_EXTRACTIVE_TEMPLATE).template_id
            yield TokenEvent(text=text)

        answer = self._finalise(
            text,
            context,
            usage=None,
            model=None,
            prompt_template_id=template_id,
            extra_degraded=degraded,
        )
        yield CompletionEvent(answer=answer)

    # -- internals --------------------------------------------------------
    def _request(
        self,
        question: str,
        context: AssembledContext,
        *,
        course_name: str,
        history: Sequence[ChatMessage],
    ) -> LLMRequest:
        system = self._prompts.render(
            _ANSWER_TEMPLATE,
            course_name=course_name,
            evidence=context.prompt_text,
        )
        messages = [ChatMessage(role="system", content=system)]
        messages.extend(history)
        messages.append(ChatMessage(role="user", content=question))
        return LLMRequest(
            task=ModelTask.TUTORING,
            messages=messages,
            temperature=self._settings.llm_temperature,
            purpose=_ANSWER_TEMPLATE,
        )

    def _refusal(self, question: str, course_name: str) -> GeneratedAnswer:
        template = self._prompts.get(_REFUSAL_TEMPLATE)
        text = self._prompts.render(
            _REFUSAL_TEMPLATE,
            course_name=course_name,
            question=question,
        )
        metrics.record_degraded(NO_EVIDENCE)
        return GeneratedAnswer(
            text=text,
            citations=[],
            grounded=False,
            degraded=[NO_EVIDENCE],
            usage=None,
            model=None,
            prompt_template_id=template.template_id,
        )

    def _extractive(
        self, question: str, context: AssembledContext, *, course_name: str
    ) -> GeneratedAnswer:
        template = self._prompts.get(_EXTRACTIVE_TEMPLATE)
        text = compose_extractive(
            context, self._prompts, question=question, course_name=course_name
        )
        available = {citation.citation_id for citation in context.citations}
        cleaned, _removed = strip_hallucinated(text, available)
        citations = _citations_for(cleaned, context)
        metrics.record_degraded(LLM_UNAVAILABLE)
        return GeneratedAnswer(
            text=cleaned,
            citations=citations,
            grounded=bool(citations),
            degraded=[LLM_UNAVAILABLE],
            usage=None,
            model=None,
            prompt_template_id=template.template_id,
        )

    def _finalise(
        self,
        text: str,
        context: AssembledContext,
        *,
        usage: LLMResponse | None,
        model: str | None,
        prompt_template_id: str,
        extra_degraded: Sequence[str],
    ) -> GeneratedAnswer:
        available = {citation.citation_id for citation in context.citations}
        with tracing.span(SPAN_OUTPUT_VALIDATION) as _record:
            cleaned, _removed = strip_hallucinated(text, available)
            report = verify_citations(cleaned, available)
            degraded = list(extra_degraded)
            if not report.valid and not is_refusal(cleaned):
                # An answer with no resolvable citation is not grounded. It is still
                # returned (it may be a useful refusal in the model's own words), but
                # the degradation is visible to the caller and logged for review.
                degraded.append(UNCITED_ANSWER)
                logger.warning("tutor_answer_uncited", prompt_template_id=prompt_template_id)
            answer = GeneratedAnswer(
                text=cleaned,
                citations=_citations_for(cleaned, context),
                grounded=bool(report.valid),
                degraded=_unique(degraded),
                usage=usage,
                model=model,
                prompt_template_id=prompt_template_id,
            )
        metrics.record_degraded(*answer.degraded)
        return answer


def compose_extractive(
    context: AssembledContext,
    templates: PromptLibrary,
    *,
    question: str = "",
    course_name: str = "",
) -> str:
    """Compose a non-empty answer from the top passages, with their citations.

    Used when the gateway is unavailable. Every excerpt carries the ``[Sn]`` id
    assigned at assembly time, so the fallback answer is verifiable by exactly
    the same citation machinery as a generated one.
    """
    excerpts: list[str] = []
    for passage in context.passages[:_EXTRACTIVE_PASSAGES]:
        location = f", page {passage.page}" if passage.page is not None else ""
        excerpt = _first_chars(passage.content, _EXTRACTIVE_CHARS)
        excerpts.append(f"[{passage.citation_id}] {passage.filename}{location}: {excerpt}")
    return templates.render(
        _EXTRACTIVE_TEMPLATE,
        course_name=course_name or "your course",
        question=question,
        excerpts="\n\n".join(excerpts),
    ).strip()


def _citations_for(text: str, context: AssembledContext) -> list[Citation]:
    by_id = {citation.citation_id: citation for citation in context.citations}
    return [
        by_id[citation_id] for citation_id in extract_citation_ids(text) if citation_id in by_id
    ]


def _first_chars(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    clipped = collapsed[:limit].rstrip()
    sentence_end = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if sentence_end > limit // 2:
        return clipped[: sentence_end + 1]
    return clipped + "..."


def _unique(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "LLM_UNAVAILABLE",
    "NO_EVIDENCE",
    "UNCITED_ANSWER",
    "AnswerGenerator",
    "CompletionEvent",
    "GeneratedAnswer",
    "StreamEvent",
    "TokenEvent",
    "compose_extractive",
]
