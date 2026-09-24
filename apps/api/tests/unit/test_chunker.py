"""Chunker tests.

The two properties that matter are that overlap is real (a suffix of one chunk
and a prefix of the next) and that chunks never cross a page boundary. Both are
asserted directly rather than inferred from a golden output, because a golden
output would hide a regression that re-breaks overlap in a different way.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from coursellm.core.config import Settings
from coursellm.core.errors import ValidationError
from coursellm.rag.analyzers import tokenize
from coursellm.rag.ingestion.chunker import (
    ChunkingConfig,
    chunk_pages,
    chunking_config_version,
)
from coursellm.rag.ingestion.parsers import ParsedPage

pytestmark = pytest.mark.unit


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class TestConfig:
    def test_overlap_at_or_above_max_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChunkingConfig(max_tokens=10, overlap_tokens=10, min_tokens=1)
        with pytest.raises(ValidationError):
            ChunkingConfig(max_tokens=10, overlap_tokens=11, min_tokens=1)

    def test_valid_config_from_settings(self) -> None:
        config = ChunkingConfig.from_settings(
            _settings(chunk_size_tokens=128, chunk_overlap_tokens=16, min_chunk_tokens=8)
        )

        assert (config.max_tokens, config.overlap_tokens, config.min_tokens) == (128, 16, 8)

    def test_version_is_a_stable_twelve_hex_digest(self) -> None:
        config = ChunkingConfig(max_tokens=100, overlap_tokens=10, min_tokens=5)
        version = chunking_config_version(config)

        assert version == chunking_config_version(config)
        assert version == config.version
        assert len(version) == 12
        assert int(version, 16) >= 0

    def test_version_changes_with_configuration(self) -> None:
        a = ChunkingConfig(max_tokens=100, overlap_tokens=10, min_tokens=5)
        b = ChunkingConfig(max_tokens=100, overlap_tokens=20, min_tokens=5)

        assert chunking_config_version(a) != chunking_config_version(b)


class TestOverlap:
    def test_overlap_is_a_true_suffix_and_prefix(self) -> None:
        config = ChunkingConfig(max_tokens=8, overlap_tokens=3, min_tokens=1)
        text = " ".join(f"Token{index} fills the sentence." for index in range(40))
        chunks = chunk_pages([ParsedPage(page=1, text=text)], config)

        assert len(chunks) >= 3
        for previous, current in pairwise(chunks):
            previous_tokens = tokenize(previous.content)
            current_tokens = tokenize(current.content)
            overlap = min(config.overlap_tokens, len(previous_tokens))
            assert current_tokens[:overlap] == previous_tokens[-overlap:]

    def test_zero_overlap_means_disjoint_chunks(self) -> None:
        config = ChunkingConfig(max_tokens=6, overlap_tokens=0, min_tokens=1)
        text = " ".join(f"unique{index}" for index in range(30))
        chunks = chunk_pages([ParsedPage(page=1, text=text)], config)

        assert len(chunks) >= 2
        for previous, current in pairwise(chunks):
            assert tokenize(current.content)[0] != tokenize(previous.content)[-1]


class TestStructure:
    def test_page_boundaries_are_respected(self) -> None:
        config = ChunkingConfig(max_tokens=1000, overlap_tokens=0, min_tokens=1)
        pages = [
            ParsedPage(page=1, text="alpha uniquepageone marker"),
            ParsedPage(page=2, text="beta uniquepagetwo marker"),
        ]
        chunks = chunk_pages(pages, config)

        assert len(chunks) == 2
        assert chunks[0].page == 1
        assert "uniquepagetwo" not in chunks[0].content
        assert chunks[1].page == 2
        assert "uniquepageone" not in chunks[1].content

    def test_chunk_index_is_contiguous_from_zero(self) -> None:
        config = ChunkingConfig(max_tokens=4, overlap_tokens=1, min_tokens=1)
        pages = [
            ParsedPage(page=1, text="one two three four five six seven eight."),
            ParsedPage(page=2, text="nine ten eleven twelve thirteen fourteen."),
        ]
        chunks = chunk_pages(pages, config)

        assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))

    def test_page_numbers_are_carried_through(self) -> None:
        config = ChunkingConfig(max_tokens=5, overlap_tokens=1, min_tokens=1)
        pages = [
            ParsedPage(page=3, text=" ".join(f"p{index}" for index in range(20))),
            ParsedPage(page=4, text=" ".join(f"q{index}" for index in range(20))),
        ]
        chunks = chunk_pages(pages, config)

        assert {chunk.page for chunk in chunks} == {3, 4}
        assert all(chunk.page in {3, 4} for chunk in chunks)


class TestSizeHandling:
    def test_token_count_matches_the_analyser(self) -> None:
        config = ChunkingConfig(max_tokens=7, overlap_tokens=2, min_tokens=1)
        text = " ".join(f"term{index} and ef_construction." for index in range(40))
        chunks = chunk_pages([ParsedPage(page=1, text=text)], config)

        assert chunks
        for chunk in chunks:
            assert chunk.token_count == len(tokenize(chunk.content))
            assert chunk.token_count > 0

    def test_hard_split_produces_continuation_chunks(self) -> None:
        # One sentence with no sentence-final punctuation: it can only be split
        # on word boundaries.
        config = ChunkingConfig(max_tokens=5, overlap_tokens=1, min_tokens=1)
        text = " ".join(f"word{index}" for index in range(30))
        chunks = chunk_pages([ParsedPage(page=1, text=text)], config)

        assert len(chunks) > 1
        assert all(set(tokenize(chunk.content)) <= set(tokenize(text)) for chunk in chunks)
        assert any(chunk.starts_mid_sentence for chunk in chunks[1:])

    def test_tiny_document_still_yields_one_chunk(self) -> None:
        config = ChunkingConfig(max_tokens=100, overlap_tokens=10, min_tokens=50)
        chunks = chunk_pages([ParsedPage(page=1, text="hi there")], config)

        assert len(chunks) == 1
        assert chunks[0].token_count == 2
        assert chunks[0].chunk_index == 0

    def test_chunks_below_min_tokens_are_dropped(self) -> None:
        config = ChunkingConfig(max_tokens=5, overlap_tokens=0, min_tokens=3)
        pages = [
            ParsedPage(page=1, text="tiny"),
            ParsedPage(page=2, text=" ".join(f"long{index}" for index in range(12)) + "."),
        ]
        chunks = chunk_pages(pages, config)

        assert chunks
        assert all(chunk.page == 2 for chunk in chunks)
        assert all(chunk.token_count >= config.min_tokens for chunk in chunks)

    def test_at_least_one_chunk_survives_when_everything_is_small(self) -> None:
        # The document is smaller than min_tokens, so the "drop small chunks"
        # rule would empty it; exactly one chunk must survive.
        config = ChunkingConfig(max_tokens=100, overlap_tokens=0, min_tokens=90)
        chunks = chunk_pages([ParsedPage(page=1, text="only a few words here")], config)

        assert len(chunks) == 1

    def test_punctuation_only_document_yields_no_chunks(self) -> None:
        config = ChunkingConfig(max_tokens=10, overlap_tokens=0, min_tokens=1)

        assert chunk_pages([ParsedPage(page=1, text="... --- !!!")], config) == []
