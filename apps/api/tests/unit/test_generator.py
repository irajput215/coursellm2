"""The guarded generator: refusal without evidence, citation verification and
extractive degradation.

The gateway is a hand-written :class:`LLMGateway` double, never a patched
``litellm``. It records the request it received, so the tests can assert both
that the delimiter wraps the evidence and that no model call happens when there
is nothing to ground an answer in.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError, UpstreamError
from coursellm.db.models.content import SourceType
from coursellm.llm import LLMRequest, LLMResponse
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import (
    EVIDENCE_CLOSE_TAG,
    AssembledContext,
    Citation,
    ContextPassage,
)
from coursellm.rag.generation.generator import (
    LLM_UNAVAILABLE,
    NO_EVIDENCE,
    UNCITED_ANSWER,
    AnswerGenerator,
    CompletionEvent,
    StreamEvent,
    TokenEvent,
)

pytestmark = pytest.mark.unit

QUESTION = "What do transformers rely on?"
PASSAGE = "Transformers rely on self attention over the whole sequence."


class RecordingGateway:
    """A deterministic :class:`LLMGateway` double that records requests."""

    def __init__(
        self,
        *,
        text: str = "",
        tokens: list[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.tokens = list(tokens or [])
        self.error = error
        self.requests: list[LLMRequest] = []
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.complete_calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return LLMResponse(
            text=self.text,
            model="scripted-model",
            provider="scripted",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.0,
            latency_ms=1.0,
            finish_reason="stop",
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        self.stream_calls += 1
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for token in self.tokens:
            yield token


def _context(passage_count: int = 1, *, content: str = PASSAGE) -> AssembledContext:
    passages = [
        ContextPassage(
            citation_id=f"S{index + 1}",
            chunk_id=uuid.UUID(int=index + 1),
            document_id=uuid.UUID(int=100),
            filename="notes.pdf",
            page=1,
            source_type=SourceType.LECTURE,
            content=content,
            token_count=len(content.split()),
            rerank_score=None,
        )
        for index in range(passage_count)
    ]
    citations = [
        Citation(
            citation_id=passage.citation_id,
            chunk_id=passage.chunk_id,
            document_id=passage.document_id,
            filename=passage.filename,
            page=passage.page,
            source_type=passage.source_type,
            quote=passage.content,
        )
        for passage in passages
    ]
    prompt_text = "\n\n".join(
        f'<untrusted_evidence id="{passage.citation_id}" source="{passage.filename}" '
        f'page="1" source_type="lecture">\n{passage.content}\n{EVIDENCE_CLOSE_TAG}'
        for passage in passages
    )
    return AssembledContext(
        passages=passages,
        prompt_text=prompt_text,
        citations=citations,
        total_tokens=len(prompt_text.split()),
        truncated=False,
        dropped=0,
    )


def _generator(settings: Settings, gateway: RecordingGateway) -> AnswerGenerator:
    return AnswerGenerator(settings, gateway, PromptLibrary(settings))


async def test_no_evidence_refuses_without_calling_the_model(settings: Settings) -> None:
    gateway = RecordingGateway(text="should never be used")
    generator = _generator(settings, gateway)

    answer = await generator.answer(QUESTION, _context(0), course_name="Physics")

    assert gateway.complete_calls == 0
    assert gateway.requests == []
    assert answer.grounded is False
    assert answer.degraded == [NO_EVIDENCE]
    assert answer.text
    assert answer.citations == []
    assert answer.prompt_template_id == "tutor.refusal@1"


async def test_uncited_answer_is_not_grounded(settings: Settings) -> None:
    gateway = RecordingGateway(text="The derivative measures the rate of change.")
    generator = _generator(settings, gateway)

    answer = await generator.answer(QUESTION, _context(), course_name="Physics")

    assert answer.grounded is False
    assert UNCITED_ANSWER in answer.degraded
    assert answer.citations == []


async def test_hallucinated_citations_are_stripped(settings: Settings) -> None:
    gateway = RecordingGateway(text="Grounded claim [S1] and an invented [S9] claim.")
    generator = _generator(settings, gateway)

    answer = await generator.answer(QUESTION, _context(), course_name="Physics")

    assert "[S1]" in answer.text
    assert "[S9]" not in answer.text
    assert [citation.citation_id for citation in answer.citations] == ["S1"]
    assert answer.grounded is True


async def test_gateway_failure_degrades_to_a_non_empty_extractive_answer(
    settings: Settings,
) -> None:
    gateway = RecordingGateway(error=ServiceUnavailableError("provider is down"))
    generator = _generator(settings, gateway)

    answer = await generator.answer(QUESTION, _context(), course_name="Physics")

    assert answer.degraded == [LLM_UNAVAILABLE]
    assert answer.text
    assert "[S1]" in answer.text
    assert [citation.citation_id for citation in answer.citations] == ["S1"]
    assert answer.grounded is True
    assert answer.usage is None


async def test_upstream_error_also_degrades(settings: Settings) -> None:
    gateway = RecordingGateway(error=UpstreamError("every model failed"))
    generator = _generator(settings, gateway)

    answer = await generator.answer(QUESTION, _context(), course_name="Physics")

    assert LLM_UNAVAILABLE in answer.degraded
    assert answer.text


async def test_the_prompt_delimits_evidence_and_keeps_the_question_separate(
    settings: Settings,
) -> None:
    gateway = RecordingGateway(text="Self attention mixes positions [S1].")
    generator = _generator(settings, gateway)

    await generator.answer(QUESTION, _context(), course_name="Physics")

    request = gateway.requests[0]
    system = request.messages[0].content
    user = request.messages[-1].content
    assert user == QUESTION

    opening = system.index("<untrusted_evidence ")
    closing = system.rindex(EVIDENCE_CLOSE_TAG)
    passage_at = system.index(PASSAGE)
    assert opening < passage_at < closing
    # The passage appears exactly once, inside the region, so it was not also
    # spliced into instruction position.
    assert system.count(PASSAGE) == 1
    assert request.temperature == settings.llm_temperature


async def test_stream_runs_verification_before_the_final_event(settings: Settings) -> None:
    gateway = RecordingGateway(tokens=["Self attention ", "mixes positions ", "[S1]."])
    generator = _generator(settings, gateway)

    events: list[StreamEvent] = [
        event async for event in generator.stream(QUESTION, _context(), course_name="Physics")
    ]

    tokens = "".join(event.text for event in events if isinstance(event, TokenEvent))
    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert tokens == "Self attention mixes positions [S1]."
    assert len(completions) == 1
    answer = completions[0].answer
    assert answer.grounded is True
    assert [citation.citation_id for citation in answer.citations] == ["S1"]


async def test_stream_without_evidence_yields_the_refusal(settings: Settings) -> None:
    gateway = RecordingGateway(tokens=["never used"])
    generator = _generator(settings, gateway)

    events: list[StreamEvent] = [
        event async for event in generator.stream(QUESTION, _context(0), course_name="Physics")
    ]

    tokens = "".join(event.text for event in events if isinstance(event, TokenEvent))
    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert "don't have enough information" in tokens
    assert completions[0].answer.degraded == [NO_EVIDENCE]
    assert gateway.stream_calls == 0


async def test_stream_failure_degrades_to_extractive(settings: Settings) -> None:
    gateway = RecordingGateway(error=ServiceUnavailableError("provider is down"))
    generator = _generator(settings, gateway)

    events: list[StreamEvent] = [
        event async for event in generator.stream(QUESTION, _context(), course_name="Physics")
    ]

    completions = [event for event in events if isinstance(event, CompletionEvent)]
    assert completions[0].answer.degraded == [LLM_UNAVAILABLE]
    assert completions[0].answer.text
    assert "[S1]" in completions[0].answer.text
