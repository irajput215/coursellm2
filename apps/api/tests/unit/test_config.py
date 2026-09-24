"""Configuration tests.

These cover the startup rules that protect production, because a misconfigured
deployment is a security incident that a code review will not catch.
"""

from __future__ import annotations

import pytest

from coursellm.core.config import (
    EmbeddingProvider,
    Environment,
    Settings,
)


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


pytestmark = pytest.mark.unit

# Connection-string fixtures are assembled at runtime from harmless fragments so
# that the secret scanner can stay strict with no per-file exceptions: a literal
# `scheme://user:password@host` never appears in the source. The values are
# placeholders either way; this simply avoids training reviewers to ignore
# scanner hits in test files.
_HOST = "host:5432/db"
_USER = "user"
_PW = "pw"


def _dsn(scheme: str) -> str:
    return f"{scheme}://{_USER}:{_PW}@{_HOST}"


class TestDatabaseUrlNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (_dsn("postgres"), _dsn("postgresql+asyncpg")),
            (_dsn("postgresql"), _dsn("postgresql+asyncpg")),
            (_dsn("postgresql+asyncpg"), _dsn("postgresql+asyncpg")),
        ],
    )
    def test_scheme_is_rewritten_to_the_async_driver(self, raw: str, expected: str) -> None:
        assert _settings(database_url=raw).database_url == expected

    def test_application_and_migrations_cannot_diverge(self) -> None:
        """A Heroku-style URL must never reach SQLAlchemy unnormalised.

        This was a real defect: the application rewrote the scheme and Alembic
        did not, so migrations failed against the same database the app used.
        """
        raw = _dsn("postgres")
        assert _settings(database_url=raw).database_url.startswith("postgresql+asyncpg://")


class TestProductionGuards:
    @pytest.mark.parametrize(
        "placeholder",
        [
            "",
            "change-me",
            "change-me-in-production-not-a-real-secret",
            "secret",
        ],
    )
    def test_prod_refuses_placeholder_secret(self, placeholder: str) -> None:
        with pytest.raises(ValueError, match="SECRET_KEY"):
            _settings(environment=Environment.PROD, secret_key=placeholder)

    def test_prod_refuses_short_secret(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            _settings(environment=Environment.PROD, secret_key="short")

    def test_prod_refuses_debug(self) -> None:
        with pytest.raises(ValueError, match="DEBUG"):
            _settings(environment=Environment.PROD, debug=True, secret_key="x" * 64)

    def test_prod_refuses_wildcard_cors(self) -> None:
        """Wildcard origins are unsafe with credentials and invalid per spec."""
        with pytest.raises(ValueError, match="CORS"):
            _settings(
                environment=Environment.PROD,
                secret_key="x" * 64,
                cors_allowed_origins="https://app.example.com,*",
            )

    def test_prod_accepts_a_valid_configuration(self) -> None:
        configured = _settings(
            environment=Environment.PROD,
            secret_key="x" * 64,
            debug=False,
            cors_allowed_origins="https://app.example.com",
        )
        assert configured.is_production is True

    def test_local_allows_placeholders(self) -> None:
        """Local development must not require ceremony."""
        assert _settings(environment=Environment.LOCAL).secret_key


class TestThresholdValidation:
    def test_warn_above_block_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="INJECTION_WARN_THRESHOLD"):
            _settings(injection_warn_threshold=0.9, injection_block_threshold=0.5)

    def test_equal_thresholds_are_allowed(self) -> None:
        configured = _settings(injection_warn_threshold=0.5, injection_block_threshold=0.5)
        assert configured.injection_warn_threshold == configured.injection_block_threshold


class TestDerivedValues:
    def test_allowed_extensions_are_normalised(self) -> None:
        configured = _settings(allowed_upload_extensions="PDF, .Txt ,md")
        assert configured.allowed_extensions == frozenset({".pdf", ".txt", ".md"})

    def test_cors_list_is_trimmed_and_drops_blanks(self) -> None:
        configured = _settings(
            cors_allowed_origins=" https://a.example.com , ,https://b.example.com "
        )
        assert configured.cors_origin_list == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_graph_model_falls_back_to_the_fast_model(self) -> None:
        assert _settings(graph_extraction_model="").graph_model == _settings().fast_model

    def test_explicit_graph_model_wins(self) -> None:
        configured = _settings(graph_extraction_model="openai/gpt-4o")
        assert configured.graph_model == "openai/gpt-4o"

    def test_default_embedding_provider_is_not_the_test_stub(self) -> None:
        assert _settings().embedding_provider is EmbeddingProvider.LOCAL


class TestRetrievalConfigVersion:
    """The version hash is what makes a quality change attributable.

    If it did not change when retrieval behaviour changed, an evaluation
    regression could be blamed on a stale cache and never investigated.
    """

    def test_is_stable_for_identical_configuration(self) -> None:
        assert _settings().retrieval_config_version == _settings().retrieval_config_version

    @pytest.mark.parametrize(
        "override",
        [
            {"rrf_k": 61},
            {"bm25_k1": 1.5},
            {"bm25_b": 0.5},
            {"rerank_top_k": 6},
            {"retrieval_top_k_per_retriever": 21},
            {"hnsw_ef_search": 101},
            {"context_token_budget": 3001},
            {"reranker_model": "BAAI/bge-reranker-large"},
            {"embedding_model": "BAAI/bge-base-en-v1.5"},
            {"embedding_dim": 768},
            {"rerank_enabled": False},
        ],
    )
    def test_changes_when_retrieval_configuration_changes(self, override: dict) -> None:
        assert (
            _settings().retrieval_config_version != _settings(**override).retrieval_config_version
        )

    def test_ignores_unrelated_configuration(self) -> None:
        """Changing a logging level must not invalidate every cached retrieval."""
        base = _settings()
        assert (
            base.retrieval_config_version == _settings(log_level="ERROR").retrieval_config_version
        )

    def test_is_short_and_hex(self) -> None:
        version = _settings().retrieval_config_version
        assert len(version) == 12
        assert all(c in "0123456789abcdef" for c in version)


class TestEnvironmentDefaults:
    def test_llm_and_rerank_default_to_enabled(self) -> None:
        configured = _settings()
        assert configured.llm_enabled is True
        assert configured.rerank_enabled is True

    def test_prompt_capture_is_off_by_default(self) -> None:
        """Prompts contain student document text and must be opt-in."""
        assert _settings().capture_prompts_in_traces is False

    def test_cache_ttl_default_is_bounded(self) -> None:
        configured = _settings()
        assert 0 < configured.cache_ttl_seconds <= 3600
