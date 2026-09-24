"""Reranker behaviour: the lexical fallback, the factory, and load failures.

No test here downloads a model. The cross-encoder is exercised only through its
construction and failure paths, with ``transformers`` or the loader patched, so
the suite stays offline and deterministic.
"""

from __future__ import annotations

import sys
import uuid

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.db.models.content import SourceType
from coursellm.rag.rerank import rerankers as rerankers_module
from coursellm.rag.rerank.rerankers import (
    LEXICAL_RERANKER_MODEL_ID,
    CrossEncoderReranker,
    LexicalReranker,
    RerankedResult,
    get_reranker,
)
from coursellm.rag.retrieval.types import SearchResult

pytestmark = pytest.mark.unit


def _result(n: int, content: str, *, rank: int = 1) -> SearchResult:
    return SearchResult(
        chunk_id=uuid.UUID(int=n),
        document_id=uuid.UUID(int=100),
        course_id=uuid.UUID(int=200),
        content=content,
        page=1,
        topic=None,
        token_count=len(content.split()),
        source_type=SourceType.LECTURE,
        rank=rank,
        score=0.5,
        retriever="lexical",
    )


# ---------------------------------------------------------------------------
# LexicalReranker
# ---------------------------------------------------------------------------
class TestLexicalReranker:
    async def test_is_deterministic(self) -> None:
        reranker = LexicalReranker()
        results = [_result(1, "attention mechanism"), _result(2, "attention"), _result(3, "other")]

        first = await reranker.rerank("attention mechanism", results, top_k=10)
        second = await reranker.rerank("attention mechanism", results, top_k=10)

        assert [(item.chunk_id, item.rerank_score) for item in first] == [
            (item.chunk_id, item.rerank_score) for item in second
        ]

    async def test_more_query_terms_outranks_fewer(self) -> None:
        reranker = LexicalReranker()
        both = _result(1, "attention mechanism")
        one = _result(2, "attention")

        ranked = await reranker.rerank("attention mechanism", [one, both], top_k=10)

        assert [item.chunk_id for item in ranked] == [both.chunk_id, one.chunk_id]
        assert ranked[0].rerank_score > ranked[1].rerank_score

    async def test_shorter_passage_wins_at_equal_coverage(self) -> None:
        reranker = LexicalReranker()
        short = _result(1, "attention")
        long = _result(2, "attention alpha beta gamma delta")

        ranked = await reranker.rerank("attention", [long, short], top_k=10)

        assert ranked[0].chunk_id == short.chunk_id
        assert ranked[0].rerank_score > ranked[1].rerank_score

    async def test_ties_are_broken_by_chunk_id_producing_a_total_order(self) -> None:
        reranker = LexicalReranker()
        # Identical content, so identical scores; input order is deliberately not sorted.
        results = [_result(3, "attention"), _result(1, "attention"), _result(2, "attention")]

        ranked = await reranker.rerank("attention", results, top_k=10)

        assert [item.chunk_id for item in ranked] == sorted(item.chunk_id for item in results)
        assert len({item.rerank_score for item in ranked}) == 1

    async def test_original_rank_reflects_the_input_position(self) -> None:
        reranker = LexicalReranker()
        results = [_result(5, "attention mechanism"), _result(6, "attention"), _result(7, "other")]

        ranked = await reranker.rerank("attention mechanism", results, top_k=10)

        positions = {result.chunk_id: index for index, result in enumerate(results, start=1)}
        assert [item.original_rank for item in ranked] == [
            positions[item.chunk_id] for item in ranked
        ]
        assert [item.new_rank for item in ranked] == [1, 2, 3]

    async def test_top_k_truncates_after_ordering(self) -> None:
        reranker = LexicalReranker()
        results = [_result(n, "attention") for n in (1, 2, 3)]

        ranked = await reranker.rerank("attention", results, top_k=2)

        assert [item.new_rank for item in ranked] == [1, 2]
        assert len(ranked) == 2

    async def test_empty_input_returns_nothing(self) -> None:
        reranker = LexicalReranker()
        assert await reranker.rerank("attention", [], top_k=5) == []
        assert await reranker.rerank("attention", [_result(1, "attention")], top_k=0) == []

    async def test_query_with_no_terms_is_still_a_total_order(self) -> None:
        reranker = LexicalReranker()
        results = [_result(2, "alpha"), _result(1, "beta")]

        ranked = await reranker.rerank("?!...", results, top_k=10)

        assert [item.chunk_id for item in ranked] == sorted(item.chunk_id for item in results)
        assert all(item.rerank_score == 0.0 for item in ranked)

    async def test_does_not_mutate_the_input_results(self) -> None:
        reranker = LexicalReranker()
        results = [_result(1, "attention mechanism", rank=1), _result(2, "other", rank=2)]
        before = [(result.chunk_id, result.rank, result.score) for result in results]

        ranked = await reranker.rerank("attention mechanism", results, top_k=10)

        assert before == [(result.chunk_id, result.rank, result.score) for result in results]
        assert all(item.source is result for item, result in zip(ranked, results, strict=False))
        assert all(isinstance(item, RerankedResult) for item in ranked)

    async def test_model_id_names_the_substitute(self) -> None:
        assert LexicalReranker().model_id == LEXICAL_RERANKER_MODEL_ID


# ---------------------------------------------------------------------------
# CrossEncoderReranker construction
# ---------------------------------------------------------------------------
class TestCrossEncoderConstruction:
    def test_raises_a_clear_error_when_transformers_is_absent(
        self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "transformers", None)
        enabled = test_settings.model_copy(update={"rerank_enabled": True})

        with pytest.raises(ServiceUnavailableError, match="transformers"):
            CrossEncoderReranker(enabled)

    def test_wraps_a_model_load_failure(
        self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Unloadable:
            @classmethod
            def from_pretrained(cls, *args: object, **kwargs: object) -> object:
                msg = "no network"
                raise OSError(msg)

        monkeypatch.setattr(
            rerankers_module, "_load_transformers", lambda: (_Unloadable, _Unloadable)
        )
        enabled = test_settings.model_copy(update={"rerank_enabled": True})

        with pytest.raises(ServiceUnavailableError, match="could not be loaded"):
            CrossEncoderReranker(enabled)


# ---------------------------------------------------------------------------
# get_reranker
# ---------------------------------------------------------------------------
class TestGetReranker:
    def test_disabled_returns_the_lexical_substitute(self, test_settings: Settings) -> None:
        reranker = get_reranker(test_settings.model_copy(update={"rerank_enabled": False}))

        assert isinstance(reranker, LexicalReranker)
        assert reranker.model_id == LEXICAL_RERANKER_MODEL_ID

    def test_enabled_returns_a_cross_encoder_when_the_model_loads(
        self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeTokenizer:
            @classmethod
            def from_pretrained(cls, model_id: str) -> _FakeTokenizer:
                return cls()

        class _FakeModel:
            @classmethod
            def from_pretrained(cls, model_id: str) -> _FakeModel:
                return cls()

            def eval(self) -> _FakeModel:
                return self

        monkeypatch.setattr(
            rerankers_module, "_load_transformers", lambda: (_FakeTokenizer, _FakeModel)
        )
        enabled = test_settings.model_copy(update={"rerank_enabled": True})

        reranker = get_reranker(enabled)

        assert isinstance(reranker, CrossEncoderReranker)
        assert reranker.model_id == enabled.reranker_model

    def test_falls_back_when_the_cross_encoder_is_unavailable(
        self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _fail(self: object, settings: Settings) -> None:
            msg = "model hub unreachable"
            raise ServiceUnavailableError(msg)

        monkeypatch.setattr(CrossEncoderReranker, "__init__", _fail)
        enabled = test_settings.model_copy(update={"rerank_enabled": True})

        reranker = get_reranker(enabled)

        assert isinstance(reranker, LexicalReranker)

    def test_falls_back_when_transformers_is_absent(
        self, test_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "transformers", None)
        enabled = test_settings.model_copy(update={"rerank_enabled": True})

        reranker = get_reranker(enabled)

        assert isinstance(reranker, LexicalReranker)
        assert reranker.model_id == LEXICAL_RERANKER_MODEL_ID
