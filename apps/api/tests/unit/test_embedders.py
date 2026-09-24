"""Embedder tests.

Only the hashing provider is exercised end to end: the point of
:class:`HashingEmbedder` is that the pipeline can be tested without a model
download, and the tests hold that line rather than skipping when a model is
absent.
"""

from __future__ import annotations

import math

import pytest

from coursellm.core.config import EmbeddingProvider, Settings
from coursellm.core.errors import ServiceUnavailableError, ValidationError
from coursellm.db.models.content import EMBEDDING_DIM
from coursellm.rag.ingestion.embedders import (
    HashingEmbedder,
    clear_embedder_cache,
    get_embedder,
)

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _isolated_embedder_cache():
    clear_embedder_cache()
    yield
    clear_embedder_cache()


class TestHashingEmbedder:
    async def test_deterministic(self) -> None:
        text = "ef_construction bge-reranker-base HNSW"
        first = HashingEmbedder(dim=64)
        second = HashingEmbedder(dim=64)

        assert await first.embed_documents([text]) == await second.embed_documents([text])

    async def test_dimension(self) -> None:
        embedder = HashingEmbedder(dim=128)
        vectors = await embedder.embed_documents(["alpha beta", "gamma"])

        assert embedder.dim == 128
        assert [len(vector) for vector in vectors] == [128, 128]

    async def test_l2_normalised(self) -> None:
        embedder = HashingEmbedder(dim=64)
        vector = await embedder.embed_query("unit norm please")

        assert math.sqrt(sum(component * component for component in vector)) == pytest.approx(
            1.0, abs=1e-9
        )

    async def test_query_and_document_paths_agree(self) -> None:
        embedder = HashingEmbedder(dim=64)
        text = "same text both ways"

        assert await embedder.embed_query(text) == (await embedder.embed_documents([text]))[0]

    async def test_different_texts_differ(self) -> None:
        embedder = HashingEmbedder(dim=64)

        assert await embedder.embed_query("alpha") != await embedder.embed_query(
            "completely different words"
        )

    async def test_empty_text_is_the_zero_vector(self) -> None:
        embedder = HashingEmbedder(dim=16)
        vector = await embedder.embed_query("")

        assert vector == [0.0] * 16

    async def test_batches_preserve_order(self) -> None:
        embedder = HashingEmbedder(dim=32)
        texts = ["one", "two", "three"]

        batched = await embedder.embed_documents(texts)
        individually = [await embedder.embed_query(text) for text in texts]

        assert batched == individually


class TestFactory:
    def test_hashing_provider_returns_hashing_embedder(self) -> None:
        embedder = get_embedder(_settings(embedding_provider=EmbeddingProvider.HASHING))

        assert isinstance(embedder, HashingEmbedder)
        assert embedder.model_id
        assert embedder.dim == EMBEDDING_DIM

    def test_factory_caches_the_instance(self) -> None:
        settings = _settings(embedding_provider=EmbeddingProvider.HASHING)

        assert get_embedder(settings) is get_embedder(settings)

    def test_dimension_mismatch_with_schema_raises(self) -> None:
        with pytest.raises(ValidationError):
            get_embedder(_settings(embedding_provider=EmbeddingProvider.HASHING, embedding_dim=128))

    def test_litellm_provider_is_not_wired_yet(self) -> None:
        with pytest.raises(ServiceUnavailableError):
            get_embedder(_settings(embedding_provider=EmbeddingProvider.LITELLM))
