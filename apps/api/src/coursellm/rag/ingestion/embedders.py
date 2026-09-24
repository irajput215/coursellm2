"""Embedding providers.

The pipeline depends on the :class:`Embedder` protocol and never on a concrete
model, which is what makes the retrieval stack testable in CI without a network
call or a multi-hundred-megabyte download.

Three providers exist, chosen by ``settings.embedding_provider``:

* ``hashing`` — :class:`HashingEmbedder`, dependency-free and deterministic.
* ``local`` — :class:`SentenceTransformerEmbedder`, the real thing.
* ``litellm`` — not wired yet; see PR 8.

The dimension guard at the bottom is the important safety property: the pgvector
column has a fixed width from the schema, so a configuration whose dimension
differs from the schema must fail loudly instead of writing vectors the index
cannot hold.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from coursellm.core.config import EmbeddingProvider, Settings
from coursellm.core.errors import ServiceUnavailableError, ValidationError
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.rag.analyzers import tokenize

# The retrieval instruction BGE models are trained with. Applied to queries
# only: prepending it to documents measurably degrades retrieval, because the
# document side of the training pairs never carried it.
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


@runtime_checkable
class Embedder(Protocol):
    """A bi-encoder that turns text into unit-norm vectors."""

    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed passages for storage."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query for retrieval."""
        ...


class HashingEmbedder:
    """A deterministic, dependency-free embedding.

    Each analysed term is hashed with BLAKE2b — a stable hash, unlike Python's
    salted ``hash()`` — and used to pick a dimension and a sign. The result is
    L2-normalised, so cosine distance behaves like a reasonable proxy for term
    overlap.

    **This is not semantically meaningful.** Two paraphrases share few terms and
    therefore land far apart. Its entire purpose is that the whole storage and
    retrieval stack can be exercised in CI without downloading a model or
    reaching the network, deterministically and in milliseconds.
    """

    def __init__(self, *, dim: int, model_id: str = "hashing-v1") -> None:
        if dim <= 0:
            raise ValidationError("HashingEmbedder dimension must be positive.")
        self._dim = dim
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            # A signed contribution reduces the bias that collisions create when
            # many distinct terms land on the same dimension.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            # An empty or punctuation-only string has no direction. Returning
            # the zero vector keeps the return type total; retrieval treats a
            # zero vector as matching nothing.
            return vector
        return [value / norm for value in vector]


class SentenceTransformerEmbedder:
    """Embeddings from ``sentence-transformers``.

    Import and model load both happen at construction, so a deployment that
    selected this provider but did not install it fails at startup with an
    actionable message rather than on the first upload.

    BGE models are handled specially in two ways: CLS pooling is forced (their
    training objective pools the class token, not the mean), and the query
    instruction is prepended to queries only.
    """

    def __init__(self, *, settings: Settings) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ServiceUnavailableError(
                "embedding_provider='local' requires the optional 'embeddings' extra "
                "(pip install 'coursellm[embeddings]')."
            ) from exc

        self._settings = settings
        self._model_id = settings.embedding_model
        self._dim = settings.embedding_dim
        self._is_bge = "bge" in settings.embedding_model.lower()

        try:
            self._model = SentenceTransformer(settings.embedding_model)
        except Exception as exc:
            raise ServiceUnavailableError(
                f"Could not load embedding model {settings.embedding_model!r}: {exc}"
            ) from exc

        if self._is_bge:
            self._force_cls_pooling()

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = await asyncio.to_thread(self._encode, list(texts), False)
        self._validate_dimensions(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await asyncio.to_thread(self._encode, [text], True)
        self._validate_dimensions(vectors)
        return vectors[0]

    # -- internals --------------------------------------------------------
    def _force_cls_pooling(self) -> None:
        for module in self._model.modules():
            target: Any = module
            if hasattr(target, "pooling_mode_cls_token"):
                target.pooling_mode_cls_token = True
                for attribute in (
                    "pooling_mode_mean_tokens",
                    "pooling_mode_max_tokens",
                    "pooling_mode_mean_sqrt_len_tokens",
                    "pooling_mode_weightedmean_tokens",
                    "pooling_mode_lasttoken",
                ):
                    if hasattr(target, attribute):
                        setattr(target, attribute, False)

    def _encode(self, texts: list[str], is_query: bool) -> list[list[float]]:
        payload = texts
        if is_query and self._is_bge:
            payload = [f"{_BGE_QUERY_INSTRUCTION}{text}" for text in texts]

        torch = self._optional_torch()
        if torch is not None:
            with torch.inference_mode():
                return self._encode_once(payload)
        return self._encode_once(payload)

    def _encode_once(self, payload: list[str]) -> list[list[float]]:
        encoded = self._model.encode(
            payload,
            batch_size=self._settings.embedding_batch_size,
            normalize_embeddings=True,  # unit norm, so cosine == inner product
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(component) for component in vector] for vector in encoded]

    @staticmethod
    def _optional_torch() -> Any | None:
        try:
            import torch
        except ImportError:
            return None
        return torch

    def _validate_dimensions(self, vectors: Sequence[Sequence[float]]) -> None:
        for vector in vectors:
            if len(vector) != self._settings.embedding_dim:
                raise ValidationError(
                    f"Embedding model {self._model_id!r} returned a {len(vector)}-dimensional "
                    f"vector but embedding_dim is {self._settings.embedding_dim}; "
                    "the model and configuration disagree."
                )


# Process-wide cache. Keyed by everything that changes the produced vectors, so
# two configurations cannot share a model instance by accident. The settings
# object itself is not hashable (pydantic models are mutable), hence the tuple.
_EMBEDDER_CACHE: dict[tuple[str, str, int], Embedder] = {}


def clear_embedder_cache() -> None:
    """Drop cached embedders. Used by tests and by a configuration reload."""
    _EMBEDDER_CACHE.clear()


def get_embedder(settings: Settings) -> Embedder:
    """Return the configured embedder, constructing and caching it on first use."""
    if settings.embedding_dim != EMBEDDING_DIM:
        raise ValidationError(
            f"The schema fixes the embedding dimension at {EMBEDDING_DIM}, but "
            f"embedding_dim is {settings.embedding_dim}. A migration that rebuilds "
            "chunk_embeddings.embedding (and its HNSW index) is required before this "
            "configuration can be used."
        )

    key = (settings.embedding_provider.value, settings.embedding_model, settings.embedding_dim)
    cached = _EMBEDDER_CACHE.get(key)
    if cached is not None:
        return cached

    embedder = _build_embedder(settings)
    _EMBEDDER_CACHE[key] = embedder
    return embedder


def _build_embedder(settings: Settings) -> Embedder:
    provider = settings.embedding_provider
    if provider is EmbeddingProvider.HASHING:
        return HashingEmbedder(dim=settings.embedding_dim)
    if provider is EmbeddingProvider.LOCAL:
        return SentenceTransformerEmbedder(settings=settings)
    if provider is EmbeddingProvider.LITELLM:
        # TODO(PR 8): route through coursellm.llm once the gateway module exists,
        # so that hosted embedding providers share the retry, routing and
        # redaction policy of the text models.
        raise ServiceUnavailableError(
            "The LiteLLM embedding gateway is not wired up yet (planned for PR 8). "
            "Set EMBEDDING_PROVIDER to 'hashing' or 'local' for now."
        )
    raise ValidationError(f"Unsupported embedding provider: {provider!r}.")
