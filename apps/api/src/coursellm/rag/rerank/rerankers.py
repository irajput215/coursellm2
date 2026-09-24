"""Cross-encoder and lexical reranking of the fused candidate list.

Both retrievers score the query and the passage *independently*: the bi-encoder
pools a passage into a vector before it ever sees the query, and BM25 is a bag
of words. A cross-encoder concatenates ``[query, passage]`` and runs full
self-attention across both, so every query token can attend to every passage
token. That is strictly more expressive and measurably more precise, at the cost
of one forward pass per candidate — which is why it is applied only to the fused
top-N.

Two implementations are provided:

* :class:`CrossEncoderReranker` — the real thing, ``BAAI/bge-reranker-base`` by
  default, loaded lazily so that the ``transformers``/``torch`` extras stay
  optional.
* :class:`LexicalReranker` — a deterministic, dependency-free stand-in used by
  the factory when reranking is disabled or the model cannot load, and by tests
  and CI so no model is ever downloaded in a test run.

The prototype reranker mutated the ``SearchResult`` objects it was given —
overwriting each row's score and rank in place — so the pre-rerank order was
lost and a failure left the candidate list half-rewritten. Neither class here
mutates its inputs: :class:`~coursellm.rag.retrieval.types.SearchResult` is a
frozen dataclass, and each returns new :class:`RerankedResult` objects that keep
the pre-rerank position alongside the new one.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from collections import Counter
from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.core.logging import get_logger
from coursellm.rag.analyzers import normalize_query, tokenize
from coursellm.rag.retrieval.types import SearchResult

logger = get_logger(__name__)

#: BGE was trained at 512 tokens and the model rejects longer inputs.
RERANK_MAX_LENGTH = 512

#: Identifier reported by the dependency-free fallback. It is deliberately not a
#: Hugging Face id so it can never be mistaken for a downloaded model.
LEXICAL_RERANKER_MODEL_ID = "lexical-fallback"

_SCORE_PRECISION = 6


@dataclass(frozen=True, slots=True)
class RerankedResult:
    """One passage after reranking, with its position before and after.

    ``rerank_score`` is the raw model output (a logit) rounded to six decimal
    places. ``original_rank`` is the 1-based position in the sequence handed to
    the reranker — the fused order — so a caller can still see what reranking
    changed and evaluation can separate a recall failure from a ranking failure.
    """

    chunk_id: uuid.UUID
    rerank_score: float
    original_rank: int
    new_rank: int
    source: SearchResult


class Reranker(Protocol):
    """The reranking contract: reorder passages for a query and keep the top ones."""

    model_id: str

    async def rerank(
        self, query: str, results: Sequence[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        """Return at most ``top_k`` results ordered by descending relevance."""
        ...


class CrossEncoderReranker:
    """A ``transformers`` cross-encoder, loaded lazily and never mutating inputs.

    Construction imports ``transformers`` and loads the configured model; if the
    package is missing or the model cannot be fetched, a
    :class:`~coursellm.core.errors.ServiceUnavailableError` is raised so the
    factory can fall back instead of taking the request down.

    ``torch`` is optional and used only for :func:`torch.inference_mode`, which
    disables autograd bookkeeping and lowers peak memory; without it the forward
    passes run normally.
    """

    def __init__(self, settings: Settings) -> None:
        self.model_id = settings.reranker_model
        self._batch_size = settings.rerank_batch_size
        tokenizer_cls, model_cls = _load_transformers()
        try:
            self._tokenizer = tokenizer_cls.from_pretrained(self.model_id)
            self._model = model_cls.from_pretrained(self.model_id)
            self._model.eval()
        except Exception as exc:
            msg = (
                f"Cross-encoder reranker '{self.model_id}' could not be loaded. "
                "Install the 'rerank' extra and check network access to the model hub."
            )
            raise ServiceUnavailableError(msg) from exc

    async def rerank(
        self, query: str, results: Sequence[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        """Score every passage with the cross-encoder and keep the best ``top_k``.

        The input sequence is never modified. Batches are bounded by
        ``settings.rerank_batch_size`` and padded/truncated to ``max_length=512``
        so a long passage cannot blow up the forward pass.
        """
        if not results or top_k <= 0:
            return []

        scores: list[float] = []
        for start in range(0, len(results), self._batch_size):
            batch = results[start : start + self._batch_size]
            inputs = self._tokenizer(
                [query] * len(batch),
                [result.content for result in batch],
                padding=True,
                truncation=True,
                max_length=RERANK_MAX_LENGTH,
                return_tensors="pt",
            )
            with _inference_mode():
                logits = self._model(**inputs).logits
            scores.extend(float(value) for value in logits.view(-1).tolist())

        if len(scores) != len(results):
            msg = (
                f"Cross-encoder reranker '{self.model_id}' returned {len(scores)} scores "
                f"for {len(results)} passages."
            )
            raise ServiceUnavailableError(msg)

        order = sorted(
            range(len(results)),
            key=lambda index: (-scores[index], results[index].chunk_id),
        )
        return [
            RerankedResult(
                chunk_id=results[index].chunk_id,
                rerank_score=round(scores[index], _SCORE_PRECISION),
                original_rank=index + 1,
                new_rank=new_rank,
                source=results[index],
            )
            for new_rank, index in enumerate(order[:top_k], start=1)
        ]


class LexicalReranker:
    """A deterministic, dependency-free fallback — a test and CI substitute.

    It is **not** a quality substitute for a cross-encoder: it re-reads the same
    bag of words BM25 already saw, so it cannot recover a passage the lexical
    retriever missed or resolve paraphrase. It exists so the pipeline, the
    degradation paths and the tests can run with no model download.

    The score, chosen to be inspectable rather than strong, is

        score(passage) = SUM over distinct query terms t of tf(t, passage)
                         / sqrt(number of tokens in passage)

    using :func:`coursellm.rag.analyzers.normalize_query` for the query terms and
    :func:`coursellm.rag.analyzers.tokenize` for the passage. Coverage rewards a
    passage that contains more of the query; the square-root length divisor
    prefers a short focused passage over a long one that happens to contain the
    same terms. Ties are broken by ascending ``chunk_id``, so the order is a
    total, reproducible one.
    """

    model_id = LEXICAL_RERANKER_MODEL_ID

    async def rerank(
        self, query: str, results: Sequence[SearchResult], *, top_k: int
    ) -> list[RerankedResult]:
        if not results or top_k <= 0:
            return []

        query_terms = set(normalize_query(query))
        scored: list[tuple[float, uuid.UUID, int, SearchResult]] = []
        for position, result in enumerate(results, start=1):
            counts = Counter(tokenize(result.content))
            matches = sum(counts.get(term, 0) for term in query_terms)
            length = math.sqrt(sum(counts.values())) if counts else 1.0
            scored.append(
                (round(matches / length, _SCORE_PRECISION), result.chunk_id, position, result)
            )
        scored.sort(key=lambda item: (-item[0], item[1]))

        return [
            RerankedResult(
                chunk_id=result.chunk_id,
                rerank_score=score,
                original_rank=position,
                new_rank=new_rank,
                source=result,
            )
            for new_rank, (score, _, position, result) in enumerate(scored[:top_k], start=1)
        ]


def get_reranker(settings: Settings) -> Reranker:
    """Build the configured reranker, falling back to the lexical substitute.

    ``rerank_enabled=False`` selects :class:`LexicalReranker` directly. Otherwise
    the cross-encoder is attempted and any
    :class:`~coursellm.core.errors.ServiceUnavailableError` (missing
    ``transformers``, unloadable model) is logged and degraded to
    :class:`LexicalReranker`, because a missing reranker must never fail a
    request.
    """
    if not settings.rerank_enabled:
        return LexicalReranker()
    try:
        return CrossEncoderReranker(settings)
    except ServiceUnavailableError as exc:
        logger.warning(
            "reranker_fallback",
            model=settings.reranker_model,
            reason=str(exc),
        )
        return LexicalReranker()


def _load_transformers() -> tuple[Any, Any]:
    """Import the cross-encoder classes, converting failure into a domain error."""
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        msg = (
            "The 'transformers' package is required for cross-encoder reranking. "
            "Install the 'rerank' extra, or set RERANK_ENABLED=false."
        )
        raise ServiceUnavailableError(msg) from exc
    return AutoTokenizer, AutoModelForSequenceClassification


def _inference_mode() -> AbstractContextManager[Any]:
    """Return ``torch.inference_mode()`` when torch is importable, else a no-op."""
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is optional
        return contextlib.nullcontext()
    return cast(AbstractContextManager[Any], torch.inference_mode())
