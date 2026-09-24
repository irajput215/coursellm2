"""Application configuration.

One source of truth for every tunable, loaded from the environment (and from a
``.env`` file in local development). Two rules are enforced here rather than
left to discipline:

1. **Production cannot start with a placeholder secret.** A misconfigured
   deployment fails loudly at boot instead of silently issuing forgeable tokens.
2. **Retrieval-affecting settings are hashed into a version string.** That hash
   is stored on every evaluation run and included in every cache key, so a
   quality change can always be attributed to a configuration change rather
   than to a stale cache. See ``docs/decisions/ADR-0010``.

Everything is a plain field on :class:`Settings`; nothing reads ``os.environ``
directly elsewhere in the codebase.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root, derived from this file's location so that it is correct
# regardless of the working directory:
#   <root>/apps/api/src/coursellm/core/config.py
#    [5]    [4]  [3] [2]   [1]   [0]
REPO_ROOT = Path(__file__).resolve().parents[5]

_PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "changeme",
        "change-me-in-production-not-a-real-secret",
        "secret",
        "your-secret-key",
    }
)

# Minimum length for a production signing key. 32 bytes of entropy is the
# lower bound for HS256 to be meaningful; token_urlsafe(32) yields 43 chars.
_MIN_PROD_SECRET_LENGTH = 32


class Environment(StrEnum):
    """Deployment environment. Controls the strictness of startup validation."""

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class EmbeddingProvider(StrEnum):
    """Where document and query vectors come from.

    ``HASHING`` is the deterministic, dependency-free provider used by CI and
    unit tests. It is *not* semantically meaningful; it exists so that the
    retrieval pipeline can be tested for correctness without downloading a
    model or making a network call.
    """

    LOCAL = "local"
    LITELLM = "litellm"
    HASHING = "hashing"


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application --------------------------------------------------------
    app_name: str = "CourseLLM"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"
    api_v1_prefix: str = "/api/v1"

    # -- Security -----------------------------------------------------------
    # Placeholder default. It is explicitly rejected by the prod validator below,
    # so shipping it is a startup failure rather than a silent vulnerability.
    secret_key: str = "change-me-in-production-not-a-real-secret"  # noqa: S105
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1, le=60 * 24 * 30)
    refresh_token_expire_days: int = Field(default=14, ge=1, le=365)

    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=60, ge=1)
    rate_limit_llm_requests_per_hour: int = Field(default=200, ge=1)

    max_query_chars: int = Field(default=2000, ge=32, le=100_000)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    # Must match what coursellm.rag.ingestion.parsers can actually parse.
    # Advertising a type that has no parser turns a clear rejection at the
    # boundary into a confusing failure after upload.
    allowed_upload_extensions: str = ".pdf,.txt,.md,.markdown,.docx"

    injection_warn_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    injection_block_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    injection_llm_classifier_enabled: bool = False

    # -- Database -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://localhost:5432/coursellm_dev"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=200)
    db_echo: bool = False
    db_statement_timeout_ms: int = Field(default=15_000, ge=100)

    # -- Cache --------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    cache_enabled: bool = True
    cache_ttl_seconds: int = Field(default=300, ge=0)

    # -- LLM gateway --------------------------------------------------------
    llm_enabled: bool = True
    primary_model: str = "openai/gpt-4o-mini"
    fallback_model: str = "anthropic/claude-3-5-haiku-latest"
    fast_model: str = "openai/gpt-4o-mini"
    reasoning_model: str = "openai/gpt-4o"
    llm_request_timeout_seconds: int = Field(default=60, ge=1, le=600)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_routing_enabled: bool = True
    llm_max_input_tokens: int = Field(default=8000, ge=256)
    llm_max_output_tokens: int = Field(default=1500, ge=64)

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""

    # -- Embeddings ---------------------------------------------------------
    embedding_provider: EmbeddingProvider = EmbeddingProvider.LOCAL
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = Field(default=384, ge=8, le=8192)
    embedding_batch_size: int = Field(default=32, ge=1, le=512)

    # -- Reranking ----------------------------------------------------------
    rerank_enabled: bool = True
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_top_k: int = Field(default=5, ge=1, le=100)
    rerank_batch_size: int = Field(default=16, ge=1, le=128)
    rerank_timeout_ms: int = Field(default=5000, ge=50, le=60_000)
    rerank_min_score: float = -8.0

    # -- Retrieval ----------------------------------------------------------
    retrieval_top_k_per_retriever: int = Field(default=20, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    bm25_k1: float = Field(default=1.2, gt=0.0, le=10.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    context_token_budget: int = Field(default=3000, ge=256)
    metadata_filtering_enabled: bool = True

    # -- Ingestion ----------------------------------------------------------
    chunk_size_tokens: int = Field(default=400, ge=32, le=4096)
    chunk_overlap_tokens: int = Field(default=80, ge=0, le=1024)
    min_chunk_tokens: int = Field(default=24, ge=1, le=512)
    ingestion_max_concurrency: int = Field(default=2, ge=1, le=32)

    # -- Knowledge graph ----------------------------------------------------
    graph_extraction_enabled: bool = True
    graph_extraction_model: str = ""
    graph_min_edge_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    graph_max_extraction_chunks_per_document: int = Field(default=200, ge=1)

    # -- Observability ------------------------------------------------------
    otel_enabled: bool = False
    otel_service_name: str = "coursellm-api"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    metrics_enabled: bool = True

    langsmith_enabled: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "coursellm"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Off by default: prompts contain student document text.
    capture_prompts_in_traces: bool = False

    # -- Evaluation ---------------------------------------------------------
    eval_dataset_path: str = "evals/datasets/golden_rag.jsonl"
    eval_baseline_path: str = "evals/reports/baseline.json"
    eval_sample_limit: int = Field(default=0, ge=0)

    # -- Prompts ------------------------------------------------------------
    prompts_dir: str = ""

    # -- CORS ---------------------------------------------------------------
    cors_allowed_origins: str = "http://localhost:5173"

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Accept Heroku-style URLs and force the async driver.

        A ``postgres://`` scheme is rejected outright by SQLAlchemy 2.x, and a
        bare ``postgresql://`` selects the synchronous driver. Both are
        rewritten here so the application and Alembic can never diverge on the
        driver, which was a real defect in the previous implementation.
        """
        if value.startswith("postgres://"):
            value = "postgresql+asyncpg://" + value[len("postgres://") :]
        elif value.startswith("postgresql://"):
            value = "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value

    @model_validator(mode="after")
    def _validate_environment_constraints(self) -> Settings:
        if self.injection_warn_threshold > self.injection_block_threshold:
            msg = (
                "INJECTION_WARN_THRESHOLD must be <= INJECTION_BLOCK_THRESHOLD "
                f"(got {self.injection_warn_threshold} > {self.injection_block_threshold})"
            )
            raise ValueError(msg)

        if self.environment is Environment.PROD:
            if self.secret_key.strip().lower() in _PLACEHOLDER_SECRETS:
                msg = (
                    "SECRET_KEY is unset or a placeholder. Refusing to start in prod. "
                    "Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(64))"'
                )
                raise ValueError(msg)
            if len(self.secret_key) < _MIN_PROD_SECRET_LENGTH:
                msg = (
                    f"SECRET_KEY must be at least {_MIN_PROD_SECRET_LENGTH} characters in prod "
                    f"(got {len(self.secret_key)})."
                )
                raise ValueError(msg)
            if self.debug:
                msg = "DEBUG must be false in prod."
                raise ValueError(msg)
            if "*" in self.cors_origin_list:
                msg = (
                    "CORS_ALLOWED_ORIGINS must not contain '*' in prod. Wildcard origins "
                    "combined with credentials is an unsafe and invalid combination."
                )
                raise ValueError(msg)

        return self

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_extensions(self) -> frozenset[str]:
        return frozenset(
            ext if ext.startswith(".") else f".{ext}"
            for ext in (part.strip().lower() for part in self.allowed_upload_extensions.split(","))
            if ext
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_prompts_dir(self) -> Path:
        configured = self.prompts_dir.strip()
        return Path(configured) if configured else REPO_ROOT / "prompts"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PROD

    @computed_field  # type: ignore[prop-decorator]
    @property
    def graph_model(self) -> str:
        """Model used for knowledge-graph extraction, defaulting to the fast model."""
        return self.graph_extraction_model.strip() or self.fast_model

    @property
    def retrieval_config_version(self) -> str:
        """Stable hash of every setting that can change retrieval results.

        Any deliberate change to these values produces a new version, which
        makes stale caches and unattributed quality shifts impossible: the
        same hash is recorded on evaluation runs and embedded in cache keys.
        """
        payload = {
            "embedding_provider": self.embedding_provider.value,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "retrieval_top_k_per_retriever": self.retrieval_top_k_per_retriever,
            "rrf_k": self.rrf_k,
            "bm25_k1": self.bm25_k1,
            "bm25_b": self.bm25_b,
            "hnsw_ef_search": self.hnsw_ef_search,
            "rerank_enabled": self.rerank_enabled,
            "reranker_model": self.reranker_model,
            "rerank_top_k": self.rerank_top_k,
            "context_token_budget": self.context_token_budget,
            "metadata_filtering_enabled": self.metadata_filtering_enabled,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return digest[:12]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is parsed once. Tests that need different
    settings should call ``get_settings.cache_clear()`` after mutating the
    environment, or construct :class:`Settings` directly.
    """
    return Settings()


# Convenience alias. Import-time evaluation is safe: ``Settings()`` performs
# validation but opens no connections and starts no threads.
settings = get_settings()
