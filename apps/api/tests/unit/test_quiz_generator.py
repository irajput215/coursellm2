"""Quiz generation with a scripted gateway and no database.

The generator's contract is about what it refuses to do as much as what it
produces: a gateway failure must produce no items, an ungrounded item must be
dropped rather than padded, and two correct options must be rejected rather than
guessed between. Those are the assertions below.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import BaseModel

from coursellm.assessment.generator import draft_from_context
from coursellm.assessment.schemas import (
    INSUFFICIENT_GROUNDED_ITEMS,
    LLM_UNAVAILABLE,
    NO_GROUNDED_ITEMS,
    STRUCTURED_OUTPUT_FAILED,
    GeneratedQuiz,
    GeneratedQuizItem,
    ItemType,
    QuizDraft,
)
from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.db.models.content import SourceType
from coursellm.llm import LLMRequest, LLMResponse
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.context import (
    AssembledContext,
    Citation,
    ContextPassage,
)

pytestmark = pytest.mark.unit

COURSE_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
CONCEPT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _response(parsed: BaseModel | None, text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        parsed=parsed,
        model="scripted",
        provider="test",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cost_usd=0.0,
        latency_ms=0.0,
        cached=False,
        fallback_used=False,
        finish_reason="stop",
    )


class ScriptedGateway:
    """A gateway that returns one prepared payload, raw text, or raises."""

    def __init__(
        self,
        payload: GeneratedQuiz | None = None,
        *,
        fail: bool = False,
        text: str | None = None,
    ) -> None:
        self.payload = payload
        self.fail = fail
        self.text = text
        self.requests: list[LLMRequest] = []

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.fail:
            raise ServiceUnavailableError("scripted provider outage")
        if self.payload is not None:
            return _response(self.payload, self.payload.model_dump_json())
        return _response(None, self.text or "")

    async def stream(self, request: LLMRequest):  # pragma: no cover - not used
        yield ""


def _assembled(*citation_ids: str) -> AssembledContext:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    passages = [
        ContextPassage(
            citation_id=citation_id,
            chunk_id=chunk_id,
            document_id=document_id,
            filename="attention-notes.txt",
            page=1,
            source_type=SourceType.NOTES,
            content=f"passage {citation_id}",
            token_count=3,
            rerank_score=1.0,
        )
        for citation_id in citation_ids
    ]
    citations = [
        Citation(
            citation_id=citation_id,
            chunk_id=chunk_id,
            document_id=document_id,
            filename="attention-notes.txt",
            page=1,
            source_type=SourceType.NOTES,
            quote=f"passage {citation_id}",
        )
        for citation_id in citation_ids
    ]
    return AssembledContext(
        passages=passages,
        prompt_text='<untrusted_evidence id="S1">passage</untrusted_evidence>',
        citations=citations,
        total_tokens=5,
        truncated=False,
        dropped=0,
    )


def _item(**overrides: object) -> GeneratedQuizItem:
    payload: dict[str, object] = {
        "item_type": "short_answer",
        "prompt": "Explain attention.",
        "model_answer": "Attention weights the inputs.",
        "citation_ids": ["S1"],
        "concept_id": None,
    }
    payload.update(overrides)
    return GeneratedQuizItem.model_validate(payload)


async def _draft(
    settings: Settings,
    gateway: ScriptedGateway,
    *,
    assembled: AssembledContext | None = None,
    n_items: int = 3,
    candidates: dict[uuid.UUID, str] | None = None,
    linked: dict[str, set[uuid.UUID]] | None = None,
    item_types: list[ItemType] | None = None,
) -> QuizDraft:
    return await draft_from_context(
        settings=settings,
        gateway=gateway,
        prompts=PromptLibrary(settings),
        assembled=assembled if assembled is not None else _assembled("S1", "S2"),
        course_id=COURSE_ID,
        course_name="Machine Learning",
        concept_candidates=candidates if candidates is not None else {CONCEPT_ID: "Attention"},
        linked_concepts_by_citation=linked if linked is not None else {},
        n_items=n_items,
        difficulty="medium",
        item_types=item_types if item_types is not None else ["short_answer"],
    )


async def test_items_are_grounded_and_carry_resolved_citation_ids(settings: Settings) -> None:
    gateway = ScriptedGateway(GeneratedQuiz(items=[_item(), _item()]))

    draft = await _draft(settings, gateway)

    assert len(draft.items) == 2
    assert all(item.citation_ids == ["S1"] for item in draft.items)
    assert [item.item_id for item in draft.items] == ["item-1", "item-2"]
    assert draft.quiz_id is None
    assert draft.shortfall == 1
    assert INSUFFICIENT_GROUNDED_ITEMS in draft.degraded


async def test_gateway_failure_yields_a_typed_failure_with_no_items(settings: Settings) -> None:
    gateway = ScriptedGateway(fail=True)

    draft = await _draft(settings, gateway, n_items=5)

    assert draft.items == []
    assert draft.shortfall == 5
    assert draft.degraded == [LLM_UNAVAILABLE]


async def test_unparseable_response_yields_a_typed_failure(settings: Settings) -> None:
    gateway = ScriptedGateway(text="this is not a quiz")

    draft = await _draft(settings, gateway)

    assert draft.items == []
    assert STRUCTURED_OUTPUT_FAILED in draft.degraded


async def test_ungrounded_items_are_dropped_and_shortfall_recorded(settings: Settings) -> None:
    payload = GeneratedQuiz(
        items=[
            _item(citation_ids=["S1"]),
            _item(citation_ids=["S9"]),
            _item(citation_ids=["S2"]),
        ]
    )
    gateway = ScriptedGateway(payload)

    draft = await _draft(settings, gateway, n_items=3)

    assert len(draft.items) == 2
    assert draft.shortfall == 1
    assert INSUFFICIENT_GROUNDED_ITEMS in draft.degraded
    assert NO_GROUNDED_ITEMS not in draft.degraded


async def test_a_response_with_no_grounded_items_is_a_failure(settings: Settings) -> None:
    gateway = ScriptedGateway(GeneratedQuiz(items=[_item(citation_ids=["S9"])]))

    draft = await _draft(settings, gateway, n_items=1)

    assert draft.items == []
    assert draft.degraded[0] == "ungrounded_item"
    assert NO_GROUNDED_ITEMS in draft.degraded


async def test_multiple_choice_with_two_correct_options_is_rejected(settings: Settings) -> None:
    ambiguous = GeneratedQuizItem(
        item_type="multiple_choice",
        prompt="Which is correct?",
        choices=["a", "b", "c", "d"],
        correct_choice_indices=[0, 2],
        citation_ids=["S1"],
    )
    gateway = ScriptedGateway(GeneratedQuiz(items=[ambiguous]))

    draft = await _draft(settings, gateway, n_items=1, item_types=["multiple_choice"])

    assert draft.items == []
    assert "invalid_multiple_choice" in draft.degraded


async def test_multiple_choice_with_one_correct_option_is_accepted(settings: Settings) -> None:
    item = GeneratedQuizItem(
        item_type="multiple_choice",
        prompt="Which is correct?",
        choices=["a", "b", "c", "d"],
        correct_choice_indices=[2],
        citation_ids=["S1"],
    )
    gateway = ScriptedGateway(GeneratedQuiz(items=[item]))

    draft = await _draft(settings, gateway, n_items=1, item_types=["multiple_choice"])

    assert len(draft.items) == 1
    assert draft.items[0].correct_choice_index == 2
    assert draft.items[0].choices == ["a", "b", "c", "d"]


async def test_option_ordering_is_stable_across_two_runs(settings: Settings) -> None:
    item = GeneratedQuizItem(
        item_type="multiple_choice",
        prompt="Which is correct?",
        choices=["gamma", "alpha", "delta", "beta"],
        correct_choice_indices=[1],
        citation_ids=["S1"],
    )
    payload = GeneratedQuiz(items=[item])

    first = await _draft(
        settings, ScriptedGateway(payload), n_items=1, item_types=["multiple_choice"]
    )
    second = await _draft(
        settings, ScriptedGateway(payload), n_items=1, item_types=["multiple_choice"]
    )

    assert first.items[0].choices == ["gamma", "alpha", "delta", "beta"]
    assert first.items[0].choices == second.items[0].choices


async def test_concept_is_attached_only_when_the_evidence_links_it(settings: Settings) -> None:
    candidates = {CONCEPT_ID: "Attention"}
    linked = await _draft(
        settings,
        ScriptedGateway(GeneratedQuiz(items=[_item(concept_id=str(CONCEPT_ID))])),
        candidates=candidates,
        linked={"S1": {CONCEPT_ID}},
        n_items=1,
    )
    unlinked = await _draft(
        settings,
        ScriptedGateway(GeneratedQuiz(items=[_item(concept_id=str(CONCEPT_ID))])),
        candidates=candidates,
        linked={"S1": set()},
        n_items=1,
    )

    assert linked.items[0].concept_id == CONCEPT_ID
    assert unlinked.items[0].concept_id is None


async def test_an_unknown_concept_id_is_never_attached(settings: Settings) -> None:
    unknown = uuid.uuid4()
    gateway = ScriptedGateway(GeneratedQuiz(items=[_item(concept_id=str(unknown))]))

    draft = await _draft(settings, gateway, linked={"S1": {unknown}}, n_items=1)

    assert draft.items[0].concept_id is None


async def test_a_supported_item_type_not_requested_is_dropped(settings: Settings) -> None:
    gateway = ScriptedGateway(
        GeneratedQuiz(items=[_item(item_type="concept_check", correct_boolean=True)])
    )

    draft = await _draft(settings, gateway, n_items=1)

    assert draft.items == []
    assert "unsupported_item_type" in draft.degraded


async def test_six_grounded_items_are_capped_at_the_requested_count(settings: Settings) -> None:
    payload = GeneratedQuiz(items=[_item() for _ in range(6)])
    gateway = ScriptedGateway(payload)

    draft = await _draft(settings, gateway, n_items=2)

    assert len(draft.items) == 2
    assert draft.shortfall == 0


def test_generated_quiz_defaults_to_no_items() -> None:
    assert GeneratedQuiz().items == []
