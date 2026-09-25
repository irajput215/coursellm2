"""The retrieval evaluation harness. No LLM is called anywhere in this module.

This is the half of the evaluation that CI can measure for real: it builds a
throwaway tenant, ingests the golden corpus through the production ingestion
pipeline with the deterministic ``hashing`` embedder, runs every golden question
through ``hybrid_search`` and ``rank`` with the deterministic
:class:`~coursellm.rag.rerank.rerankers.LexicalReranker`, and computes the
retrieval, citation and system metrics from the results.

Everything the report claims is either a number this harness computed or an
entry in ``not_measured`` with a reason. There is no third option. In
particular:

* the retrieval runner generates no answer, so citation metrics are recorded as
  ``not_measured`` with reason ``no_generation``;
* no language model is involved, so token, cost and LLM-fallback metrics are
  recorded as ``not_measured`` with reason ``no_llm``.

The report records the dataset hash, the corpus hash, the retrieval
configuration version, the embedding provider and model, the reranker id, the
git commit when one can be read from ``.git``, a timestamp, every per-question
result, and the aggregate metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text

from coursellm.core.config import REPO_ROOT, EmbeddingProvider, Settings
from coursellm.db.models.content import (
    EMBEDDING_DIM,
    Chunk,
    ChunkEmbedding,
    Course,
    Document,
    SourceType,
)
from coursellm.db.models.identity import Tenant, User, UserRole
from coursellm.db.session import dispose_engine, get_session_factory
from coursellm.db.tenancy import TenantScope, tenant_session
from coursellm.rag.fusion.rrf import fuse
from coursellm.rag.generation.citations import extract_citation_ids
from coursellm.rag.generation.context import ChunkPosition, DocumentMeta
from coursellm.rag.ingestion.embedders import Embedder, HashingEmbedder
from coursellm.rag.ingestion.pipeline import ingest_document
from coursellm.rag.rerank.pipeline import RankingOutcome, rank
from coursellm.rag.rerank.rerankers import LEXICAL_RERANKER_MODEL_ID, LexicalReranker, Reranker
from coursellm.rag.retrieval import RetrievalFilters, hybrid_search
from coursellm.rag.retrieval.types import RetrievalOutcome
from evals.judges.llm_judge import SCRIPTED_JUDGE_REASON, Judge
from evals.metrics.citations import citation_metrics_from_answer
from evals.metrics.generation import (
    Measured,
    answer_correctness,
    answer_relevance,
    faithfulness,
    measure,
)
from evals.metrics.retrieval import (
    context_precision,
    context_recall,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from evals.metrics.system import (
    TokenUsage,
    degradation_histogram,
    error_rate,
    latency_percentiles,
    rate,
    token_summary,
)

SCHEMA_VERSION: Final = "1.0"
#: Recorded in the report so a reader knows why a metric is absent.
NO_LLM: Final = "no_llm"
NO_GENERATION: Final = "no_generation"
NO_PROVIDER_KEY: Final = "no_provider_key"

#: A sentinel value stored as ``users.hashed_password`` for the evaluation
#: tenant. It is not a password and cannot be produced by the hasher, so the
#: throwaway owner can never authenticate. Kept as a named constant so no
#: reader or scanner mistakes it for a credential.
_NO_LOGIN_HASH_SENTINEL: Final = "!evaluation-tenant-no-login!"

Category = Literal["factual", "multi_hop", "identifier", "unanswerable"]

#: A callable that returns the next identifier for a row the harness creates.
IdFactory = Callable[[], uuid.UUID]


# ---------------------------------------------------------------------------
# Dataset models
# ---------------------------------------------------------------------------
class GoldenEntry(BaseModel):
    """One golden question with its expected answer and source documents."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    question: str
    expected_answer: str
    expected_sources: list[str]
    category: Category
    notes: str = ""


class DatasetInfo(BaseModel):
    """Provenance for the golden dataset file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str
    entries: int
    answerable_entries: int
    unanswerable_entries: int


class CorpusInfo(BaseModel):
    """Provenance for the ingested corpus."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: str
    sha256: str
    documents: int
    chunks: int
    filenames: list[str]


class ConfigSnapshot(BaseModel):
    """The retrieval-affecting configuration a run was produced under."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_config_version: str
    embedding_provider: str
    embedding_model: str
    embedding_dim: int
    reranker: str
    rerank_enabled: bool
    rerank_top_k: int
    retrieval_top_k_per_retriever: int
    rrf_k: int
    bm25_k1: float
    bm25_b: float
    hnsw_ef_search: int
    context_token_budget: int
    # Both are inputs to ``retrieval_config_version``. Omitting them meant a
    # reader could see the hash without being able to see two of the settings
    # that produce it, which defeats the point of recording it.
    rerank_min_score: float
    metadata_filtering_enabled: bool
    chunk_size_tokens: int
    chunk_overlap_tokens: int
    min_chunk_tokens: int


class EnvironmentInfo(BaseModel):
    """The machine a run was measured on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    machine: str
    platform: str
    python: str
    processor: str


class RetrievedHit(BaseModel):
    """One passage in the report, with the evidence for its position."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int
    chunk_id: str
    document: str
    score: float
    rerank_score: float | None = None
    semantic_rank: int | None = None
    lexical_rank: int | None = None


class QuestionResult(BaseModel):
    """Everything measured for one golden question."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    question: str
    expected_sources: list[str]
    answerable: bool
    retrieved: list[RetrievedHit]
    candidate_documents: list[str]
    metrics: dict[str, float] = Field(default_factory=dict)
    degraded: list[str] = Field(default_factory=list)
    latency_ms: dict[str, float] = Field(default_factory=dict)
    # Generation fields are populated by ``run_rag_eval`` and are absent here.
    answer: str | None = None
    cited: list[str] = Field(default_factory=list)
    refused: bool | None = None
    citation_ids_offered: list[str] = Field(default_factory=list)
    #: Only populated when a real LLM judge ran. Left empty under the scripted
    #: judge so its token-overlap stub can never be read as a quality score.
    judge_scores: dict[str, float] = Field(default_factory=dict)


class EvalReport(BaseModel):
    """The committed artefact. Every field is either measured or explained."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    kind: Literal["retrieval", "rag"]
    run_id: str
    started_at: str
    finished_at: str
    duration_s: float
    git_sha: str | None
    dataset: DatasetInfo
    corpus: CorpusInfo
    config: ConfigSnapshot
    environment: EnvironmentInfo
    judge: Literal["none", "llm", "scripted"]
    generation_mode: Literal["none", "llm", "extractive_fallback"]
    metrics: dict[str, float] = Field(default_factory=dict)
    not_measured: dict[str, str] = Field(default_factory=dict)
    degradation_reasons: dict[str, int] = Field(default_factory=dict)
    per_question: list[QuestionResult] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    """One corpus file as read from disk."""

    filename: str
    data: bytes


@dataclass(frozen=True, slots=True)
class GenerationRecord:
    """What a generation strategy produced for one question."""

    answer: str
    offered: tuple[str, ...]
    relevant_offered: tuple[str, ...]
    refused: bool
    degraded: tuple[str, ...]
    usages: tuple[TokenUsage, ...]
    mode: str


#: A generation strategy is injected so the retrieval runner stays LLM-free and
#: ``run_rag_eval`` can add generation without a second copy of the loop.
GenerateFn = Callable[
    [
        GoldenEntry,
        RankingOutcome,
        Mapping[uuid.UUID, DocumentMeta],
        Mapping[uuid.UUID, ChunkPosition],
    ],
    Awaitable[GenerationRecord | None],
]


# ---------------------------------------------------------------------------
# Loading and hashing
# ---------------------------------------------------------------------------
def load_golden_entries(path: Path) -> list[GoldenEntry]:
    """Parse the JSONL golden file, failing on any malformed line."""
    entries: list[GoldenEntry] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = GoldenEntry.model_validate_json(stripped)
            except Exception as exc:
                msg = f"{path}:{number} is not a valid golden entry: {exc}"
                raise ValueError(msg) from exc
            if entry.id in seen:
                msg = f"{path}:{number} repeats id {entry.id!r}"
                raise ValueError(msg)
            if entry.category == "unanswerable" and entry.expected_sources:
                msg = f"{path}:{number} is unanswerable but lists expected sources"
                raise ValueError(msg)
            if entry.category != "unanswerable" and not entry.expected_sources:
                msg = f"{path}:{number} is answerable but lists no expected sources"
                raise ValueError(msg)
            seen.add(entry.id)
            entries.append(entry)
    if not entries:
        msg = f"{path} contains no golden entries"
        raise ValueError(msg)
    return entries


def load_corpus(corpus_dir: Path) -> list[CorpusDocument]:
    """Read every ``.md`` file in ``corpus_dir``, sorted by filename."""
    documents = [
        CorpusDocument(filename=path.name, data=path.read_bytes())
        for path in sorted(corpus_dir.glob("*.md"))
    ]
    if not documents:
        msg = f"{corpus_dir} contains no Markdown corpus documents"
        raise ValueError(msg)
    return documents


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def dataset_hash(path: Path) -> str:
    """Hash the dataset file bytes."""
    return _sha256_bytes(path.read_bytes())


def corpus_hash(documents: Sequence[CorpusDocument]) -> str:
    """Hash the corpus as a sorted ``filename\\0content\\0`` stream."""
    digest = hashlib.sha256()
    for document in sorted(documents, key=lambda item: item.filename):
        digest.update(document.filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.data)
        digest.update(b"\0")
    return digest.hexdigest()


def git_sha(repo_root: Path = REPO_ROOT) -> str | None:
    """Read the current commit from ``.git`` without invoking git.

    Returns ``None`` rather than raising when the repository metadata is absent
    (a source tarball, a container build). The report records the absence
    honestly instead of inventing a value.
    """
    head = repo_root / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not content:
        return None
    if content.startswith("ref: "):
        ref_path = repo_root / ".git" / content[len("ref: ") :]
        try:
            return ref_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return content


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def eval_settings(base: Settings) -> Settings:
    """Force the deterministic, dependency-free evaluation configuration.

    The ``hashing`` embedder and the :class:`LexicalReranker` need no model
    download and no network, and are reproducible. Both are recorded in the
    report, and the retrieval configuration version is computed from these
    values, so a run cannot be confused with one that used a real model.
    """
    return base.model_copy(
        update={
            "embedding_provider": EmbeddingProvider.HASHING,
            "embedding_model": "hashing-v1",
            "embedding_dim": EMBEDDING_DIM,
            "rerank_enabled": True,
            "reranker_model": LEXICAL_RERANKER_MODEL_ID,
        }
    )


def resolve_dataset_path(settings: Settings, override: str | None = None) -> Path:
    """Resolve the dataset path, honouring ``EVAL_DATASET_PATH`` via settings."""
    raw = override or settings.eval_dataset_path
    path = Path(raw)
    return path if path.is_absolute() else REPO_ROOT / path


def config_snapshot(settings: Settings) -> ConfigSnapshot:
    """Capture the retrieval-affecting settings recorded with every run."""
    return ConfigSnapshot(
        retrieval_config_version=settings.retrieval_config_version,
        embedding_provider=settings.embedding_provider.value,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        reranker=settings.reranker_model,
        rerank_enabled=settings.rerank_enabled,
        rerank_top_k=settings.rerank_top_k,
        retrieval_top_k_per_retriever=settings.retrieval_top_k_per_retriever,
        rrf_k=settings.rrf_k,
        bm25_k1=settings.bm25_k1,
        bm25_b=settings.bm25_b,
        hnsw_ef_search=settings.hnsw_ef_search,
        context_token_budget=settings.context_token_budget,
        rerank_min_score=settings.rerank_min_score,
        metadata_filtering_enabled=settings.metadata_filtering_enabled,
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        min_chunk_tokens=settings.min_chunk_tokens,
    )


# ---------------------------------------------------------------------------
# Deterministic identifiers
# ---------------------------------------------------------------------------
class _DeterministicIds:
    """A counter that hands out UUIDs, standing in for :func:`uuid.uuid4`."""

    def __init__(self, seed: int = 1) -> None:
        self._next = seed

    def __call__(self, *_args: Any) -> uuid.UUID:
        # SQLAlchemy may pass an execution context to a column default; the
        # value is irrelevant to an identifier counter.
        value = uuid.UUID(int=self._next)
        self._next += 1
        return value


#: Models whose primary key the ingestion path generates without the caller
#: supplying one. Their defaults are swapped for the deterministic counter
#: below; the server-side ``gen_random_uuid()`` fallback is never reached
#: because an ORM insert always supplies the Python-side default.
_ID_MODELS: tuple[type[Any], ...] = (Tenant, User, Course, Document, Chunk, ChunkEmbedding)


@contextmanager
def deterministic_row_ids(ids: IdFactory) -> Iterator[None]:
    """Point the ORM UUID defaults at ``ids`` for the duration of a run.

    Fusion and the lexical reranker break exact score ties by chunk id, and
    production generates those ids randomly. Two evaluation runs would then
    disagree about items the system scored identically, which makes a
    regression gate meaningless. A counter makes a run reproducible without
    touching any ranking logic. The production code path is unchanged: the
    original defaults are restored on exit.
    """
    originals: list[tuple[Any, Any, Any]] = []
    for model in _ID_MODELS:
        column = cast(Any, model.__table__.c.id)
        default = column.default
        if default is None:  # pragma: no cover - every listed model has one
            continue
        originals.append((column, default, default.arg))
        default.arg = ids
        # SQLAlchemy memoises the callable in the column's default-description
        # tuple; without this invalidation the swap is never observed.
        column.__dict__.pop("_default_description_tuple", None)
    try:
        yield
    finally:
        for column, default, original in originals:
            default.arg = original
            column.__dict__.pop("_default_description_tuple", None)


@asynccontextmanager
async def deterministic_test_ids() -> AsyncIterator[_DeterministicIds]:
    """Async wrapper so the harness can install the counter with ``async with``."""
    ids = _DeterministicIds()
    with deterministic_row_ids(ids):
        yield ids


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Anchor:
    """The identifiers created for one corpus document."""

    document_id: uuid.UUID
    filename: str
    source_type: SourceType


async def _create_tenant(settings: Settings, *, label: str, slug: str, ids: IdFactory) -> uuid.UUID:
    """Create a throwaway tenant (the one table outside the RLS boundary)."""
    tenant_id = ids()
    factory = await get_session_factory(settings)
    async with factory() as session, session.begin():
        session.add(Tenant(id=tenant_id, name=label, slug=slug))
    return tenant_id


async def _seed_tenant(
    settings: Settings,
    tenant_id: uuid.UUID,
    *,
    corpus_documents: int,
    email: str,
    ids: IdFactory,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create the user and course for the evaluation tenant."""
    scope = TenantScope(tenant_id)
    user_id = ids()
    course_id = ids()
    async with tenant_session(settings, scope) as session:
        session.add(
            User(
                id=user_id,
                tenant_id=tenant_id,
                email=email,
                # Not a credential: the row exists so the tenant has an owner
                # and can never authenticate. Named here so no scanner or reader
                # mistakes a sentinel for a password.
                hashed_password=_NO_LOGIN_HASH_SENTINEL,
                full_name="Evaluation Harness",
                role=UserRole.OWNER,
                is_active=True,
            )
        )
        # The session factory sets ``autoflush=False``, so the user row must be
        # flushed before the course insert or the foreign key is unresolved.
        await session.flush()
        session.add(
            Course(
                id=course_id,
                tenant_id=tenant_id,
                user_id=user_id,
                name=f"Evaluation course ({corpus_documents} documents)",
                code="EVAL",
            )
        )
    return user_id, course_id


async def _ingest_corpus(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    documents: Sequence[CorpusDocument],
    embedder: Embedder,
    ids: IdFactory,
) -> tuple[list[_Anchor], int]:
    """Ingest every corpus document through the real pipeline; return anchors and chunk count."""
    scope = TenantScope(tenant_id)
    anchors: list[_Anchor] = []
    chunk_total = 0
    for corpus_document in documents:
        document_id = ids()
        async with tenant_session(settings, scope) as session:
            document = Document(
                id=document_id,
                tenant_id=tenant_id,
                course_id=course_id,
                user_id=user_id,
                filename=corpus_document.filename,
                storage_key=f"{tenant_id.hex}/{document_id.hex}.md",
                content_type="text/markdown",
                size_bytes=len(corpus_document.data),
                sha256=_sha256_bytes(corpus_document.data),
                source_type=SourceType.DOCUMENTATION,
            )
            session.add(document)
            await session.flush()
            result = await ingest_document(
                session,
                settings,
                document=document,
                data=corpus_document.data,
                embedder=embedder,
                # The evaluation corpus is curated and version-controlled, and it
                # deliberately contains prompt-injection *examples*. The detector
                # still records their score and classes, but a blunt quarantine
                # would remove the evidence the corpus exists to provide.
                quarantine_enabled=False,
            )
            chunk_total += result.chunk_count
            anchors.append(
                _Anchor(
                    document_id=document_id,
                    filename=corpus_document.filename,
                    source_type=SourceType.DOCUMENTATION,
                )
            )
    return anchors, chunk_total


async def _load_chunk_positions(
    settings: Settings, *, tenant_id: uuid.UUID
) -> dict[uuid.UUID, ChunkPosition]:
    """Read chunk positions so the context assembler can merge adjacent chunks."""
    scope = TenantScope(tenant_id)
    async with tenant_session(settings, scope) as session:
        rows = (
            await session.execute(
                select(
                    Chunk.id,
                    Chunk.chunk_index,
                    Chunk.starts_mid_sentence,
                ).where(Chunk.tenant_id == tenant_id)
            )
        ).all()
    return {
        row.id: ChunkPosition(index=row.chunk_index, starts_mid_sentence=row.starts_mid_sentence)
        for row in rows
    }


async def _delete_tenant(settings: Settings, tenant_id: uuid.UUID) -> bool:
    """Remove the throwaway tenant and every row that cascades from it."""
    try:
        factory = await get_session_factory(settings)
        async with factory() as session, session.begin():
            await session.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
            )
    except Exception:
        return False
    return True


def _question_metrics(
    *,
    settings: Settings,
    semantic: RetrievalOutcome,
    lexical: RetrievalOutcome,
    ranked: RankingOutcome,
    expected_sources: set[str],
    chunk_document: Mapping[str, str],
) -> dict[str, float]:
    """Compute every retrieval metric for one question.

    ``chunk_document`` maps a chunk id (as a string) to its document filename,
    so relevance is decided by the parent document as the golden entry
    specifies. Ranks come from three pools: each first-stage retriever, the
    full fused candidate list, and the final post-rerank list.
    """
    relevant = {
        chunk_id for chunk_id, filename in chunk_document.items() if filename in expected_sources
    }

    semantic_ids = [str(result.chunk_id) for result in semantic.results]
    lexical_ids = [str(result.chunk_id) for result in lexical.results]
    fused = fuse(
        semantic=semantic,
        lexical=lexical,
        k=settings.rrf_k,
        top_n=settings.retrieval_top_k_per_retriever,
    )
    fused_ids = [str(item.chunk_id) for item in fused.results]
    final_ids = [str(passage.chunk_id) for passage in ranked.results]

    k_final = settings.rerank_top_k
    return {
        "retrieval.semantic_recall_at_20": recall_at_k(semantic_ids, relevant, 20),
        "retrieval.lexical_recall_at_20": recall_at_k(lexical_ids, relevant, 20),
        "retrieval.fused_recall_at_10": recall_at_k(fused_ids, relevant, 10),
        "retrieval.fused_mrr": mrr(fused_ids, relevant),
        "retrieval.precision_at_5": precision_at_k(final_ids, relevant, k_final),
        "retrieval.recall_at_5": recall_at_k(final_ids, relevant, k_final),
        "retrieval.mrr_at_5": mrr(final_ids, relevant, k=k_final),
        "retrieval.ndcg_at_5": ndcg_at_k(final_ids, relevant, k_final),
        "retrieval.context_precision": context_precision(final_ids, relevant, k_final),
        "retrieval.context_recall": context_recall(final_ids, relevant, k_final),
    }


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean, or ``0.0`` for an empty sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


async def _judge_metrics(
    judge: Judge,
    entry: GoldenEntry,
    *,
    answer: str,
    contexts: Sequence[str],
) -> dict[str, float]:
    """Run the three generation metrics; only meaningful with a real judge."""
    faith = await measure(
        faithfulness, judge, question=entry.question, answer=answer, contexts=contexts
    )
    relevance = await measure(answer_relevance, judge, question=entry.question, answer=answer)
    correctness = await measure(
        answer_correctness,
        judge,
        question=entry.question,
        answer=answer,
        expected_answer=entry.expected_answer,
    )
    scores: dict[str, float] = {}
    for key, outcome in (
        ("generation.faithfulness", faith),
        ("generation.answer_relevance", relevance),
        ("generation.answer_correctness", correctness),
    ):
        if isinstance(outcome, Measured):
            scores[key] = outcome.value
    return scores


async def run_retrieval_eval(
    settings: Settings,
    *,
    dataset_path: Path,
    corpus_dir: Path,
    limit: int = 0,
    judge: Judge | None = None,
    generate: GenerateFn | None = None,
    reranker: Reranker | None = None,
    keep_tenant: bool = False,
    kind: Literal["retrieval", "rag"] = "retrieval",
    run_id: str | None = None,
    git_revision: str | None = None,
) -> EvalReport:
    """Run the harness end to end and return a validated report.

    ``judge`` and ``generate`` are the only difference between the
    retrieval-only and full runs. When either is absent, the metrics that
    depend on it are recorded in ``not_measured`` rather than estimated.
    """
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    entries = load_golden_entries(dataset_path)
    if limit > 0:
        entries = entries[:limit]
    documents = load_corpus(corpus_dir)

    embedder = HashingEmbedder(dim=EMBEDDING_DIM)
    active_reranker = reranker if reranker is not None else LexicalReranker()
    resolved_run_id = run_id or uuid.uuid4().hex
    run_token = uuid.uuid4().hex[:12]
    async with deterministic_test_ids() as ids:
        tenant_id = await _create_tenant(
            settings, label="CourseLLM evaluation", slug=f"eval-{run_token}", ids=ids
        )
        anchors: list[_Anchor] = []
        chunk_total = 0
        try:
            user_id, course_id = await _seed_tenant(
                settings,
                tenant_id,
                corpus_documents=len(documents),
                email=f"eval-{run_token}@example.invalid",
                ids=ids,
            )
            anchors, chunk_total = await _ingest_corpus(
                settings,
                tenant_id=tenant_id,
                course_id=course_id,
                user_id=user_id,
                documents=documents,
                embedder=embedder,
                ids=ids,
            )
            positions = await _load_chunk_positions(settings, tenant_id=tenant_id)
            filename_by_document = {anchor.document_id: anchor.filename for anchor in anchors}
            documents_by_id = {
                anchor.document_id: DocumentMeta(
                    document_id=anchor.document_id,
                    filename=anchor.filename,
                    source_type=anchor.source_type,
                )
                for anchor in anchors
            }

            per_question: list[QuestionResult] = []
            metric_samples: dict[str, list[float]] = {}
            citation_precision_samples: list[float] = []
            citation_recall_samples: list[float] = []
            hallucination_samples: list[float] = []
            stage_timings: dict[str, list[float]] = {
                "semantic": [],
                "lexical": [],
                "fusion": [],
                "rerank": [],
            }
            degradation_reasons: list[str] = []
            usages: list[TokenUsage] = []
            retrieval_degraded_questions = 0
            generation_degraded_questions = 0
            answered_questions = 0

            scope = TenantScope(tenant_id)
            for entry in entries:
                async with tenant_session(settings, scope) as session:
                    semantic, lexical = await hybrid_search(
                        session,
                        scope,
                        settings,
                        query=entry.question,
                        filters=RetrievalFilters(course_id=course_id),
                    )
                    ranked = await rank(
                        query=entry.question,
                        semantic=semantic,
                        lexical=lexical,
                        settings=settings,
                        reranker=active_reranker,
                    )

                degraded = [*semantic.degraded, *lexical.degraded, *ranked.degraded]
                degradation_reasons.extend(degraded)
                if degraded:
                    retrieval_degraded_questions += 1
                stage_timings["semantic"].append(semantic.latency_ms)
                stage_timings["lexical"].append(lexical.latency_ms)
                stage_timings["fusion"].append(ranked.stage_latency_ms.get("fusion", 0.0))
                stage_timings["rerank"].append(ranked.stage_latency_ms.get("rerank", 0.0))

                chunk_document: dict[str, str] = {}
                for search_result in [*semantic.results, *lexical.results]:
                    chunk_document[str(search_result.chunk_id)] = filename_by_document.get(
                        search_result.document_id, ""
                    )
                for passage in ranked.results:
                    chunk_document[str(passage.chunk_id)] = filename_by_document.get(
                        passage.source.document_id, ""
                    )

                candidate_documents = sorted(
                    {
                        filename_by_document[search_result.document_id]
                        for search_result in [*semantic.results, *lexical.results]
                        if search_result.document_id in filename_by_document
                    }
                )
                expected = set(entry.expected_sources)
                answerable = entry.category != "unanswerable"

                metrics: dict[str, float] = {}
                if answerable:
                    metrics = _question_metrics(
                        settings=settings,
                        semantic=semantic,
                        lexical=lexical,
                        ranked=ranked,
                        expected_sources=expected,
                        chunk_document=chunk_document,
                    )
                    for key, value in metrics.items():
                        metric_samples.setdefault(key, []).append(value)

                question_result = QuestionResult(
                    id=entry.id,
                    category=entry.category,
                    question=entry.question,
                    expected_sources=list(entry.expected_sources),
                    answerable=answerable,
                    retrieved=[
                        RetrievedHit(
                            rank=passage.final_rank,
                            chunk_id=str(passage.chunk_id),
                            document=filename_by_document.get(passage.source.document_id, ""),
                            score=passage.rrf_score,
                            rerank_score=passage.rerank_score,
                            semantic_rank=passage.semantic_rank,
                            lexical_rank=passage.lexical_rank,
                        )
                        for passage in ranked.results
                    ],
                    candidate_documents=candidate_documents,
                    metrics=metrics,
                    degraded=degraded,
                    latency_ms={
                        "semantic": round(semantic.latency_ms, 6),
                        "lexical": round(lexical.latency_ms, 6),
                        "fusion": round(ranked.stage_latency_ms.get("fusion", 0.0), 6),
                        "rerank": round(ranked.stage_latency_ms.get("rerank", 0.0), 6),
                    },
                )

                if generate is not None:
                    record = await generate(entry, ranked, documents_by_id, positions)
                    if record is not None:
                        answered_questions += 1
                        usages.extend(record.usages)
                        question_result.answer = record.answer
                        question_result.cited = extract_citation_ids(record.answer)
                        question_result.refused = record.refused
                        question_result.citation_ids_offered = list(record.offered)
                        question_result.degraded = list(
                            dict.fromkeys([*question_result.degraded, *record.degraded])
                        )
                        if record.degraded:
                            generation_degraded_questions += 1
                            degradation_reasons.extend(record.degraded)
                        offered = set(record.offered)
                        citation = citation_metrics_from_answer(
                            record.answer,
                            offered=offered,
                            relevant_offered=set(record.relevant_offered),
                        )
                        if answerable:
                            citation_precision_samples.append(citation.precision)
                            citation_recall_samples.append(citation.recall)
                        hallucination_samples.append(citation.hallucination_rate)
                        question_result.metrics.update(
                            {
                                "citations.precision": citation.precision,
                                "citations.recall": citation.recall,
                                "citations.hallucination_rate": citation.hallucination_rate,
                            }
                        )
                        if judge is not None:
                            contexts = [passage.source.content for passage in ranked.results]
                            judged = await _judge_metrics(
                                judge, entry, answer=record.answer, contexts=contexts
                            )
                            if judge.is_llm:
                                question_result.judge_scores = judged
                                question_result.metrics.update(judged)
                                for key, value in judged.items():
                                    metric_samples.setdefault(key, []).append(value)

                per_question.append(question_result)

            measured: dict[str, float] = {}
            not_measured: dict[str, str] = {}
            for key, samples in sorted(metric_samples.items()):
                measured[key] = round(_mean(samples), 6)

            latency = latency_percentiles(stage_timings)
            measured.update({f"system.{key}": value for key, value in latency.items()})
            measured["system.degradation_rate"] = round(
                rate(retrieval_degraded_questions, len(entries)), 6
            )
            measured["system.error_rate"] = round(error_rate(0, len(entries)), 6)

            if generate is None:
                for key in (
                    "citations.precision",
                    "citations.recall",
                    "citations.hallucination_rate",
                ):
                    not_measured[key] = NO_GENERATION
                for key in (
                    "generation.faithfulness",
                    "generation.answer_relevance",
                    "generation.answer_correctness",
                ):
                    not_measured[key] = NO_LLM
                for key in (
                    "system.prompt_tokens",
                    "system.completion_tokens",
                    "system.total_tokens",
                    "system.cost_usd",
                    "system.priced_fraction",
                    "system.llm_fallback_rate",
                ):
                    not_measured[key] = NO_LLM
            else:
                measured["citations.precision"] = round(_mean(citation_precision_samples), 6)
                measured["citations.recall"] = round(_mean(citation_recall_samples), 6)
                measured["system.generation_degradation_rate"] = round(
                    rate(generation_degraded_questions, answered_questions), 6
                )
                measured["citations.hallucination_rate"] = round(_mean(hallucination_samples), 6)
                if judge is None or not judge.is_llm:
                    for key in (
                        "generation.faithfulness",
                        "generation.answer_relevance",
                        "generation.answer_correctness",
                    ):
                        not_measured[key] = SCRIPTED_JUDGE_REASON if judge is not None else NO_LLM
                if usages:
                    summary = token_summary(usages)
                    measured["system.prompt_tokens"] = float(summary.prompt_tokens)
                    measured["system.completion_tokens"] = float(summary.completion_tokens)
                    measured["system.total_tokens"] = float(summary.total_tokens)
                    measured["system.cost_usd"] = summary.cost_usd
                    measured["system.priced_fraction"] = round(summary.priced_fraction, 6)
                    measured["system.llm_fallback_rate"] = round(summary.fallback_rate, 6)
                else:
                    for key in (
                        "system.prompt_tokens",
                        "system.completion_tokens",
                        "system.total_tokens",
                        "system.cost_usd",
                        "system.priced_fraction",
                        "system.llm_fallback_rate",
                    ):
                        not_measured[key] = NO_PROVIDER_KEY

            judge_mode: Literal["none", "llm", "scripted"]
            if judge is None:
                judge_mode = "none"
            elif judge.is_llm:
                judge_mode = "llm"
            else:
                judge_mode = "scripted"
            generation_mode: Literal["none", "llm", "extractive_fallback"] = (
                "none" if generate is None else "llm" if usages else "extractive_fallback"
            )

            finished_at = datetime.now(UTC).isoformat()
            return EvalReport(
                schema_version=SCHEMA_VERSION,
                kind=kind,
                run_id=resolved_run_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_s=round(time.perf_counter() - started, 6),
                git_sha=git_revision if git_revision is not None else git_sha(),
                dataset=DatasetInfo(
                    path=str(dataset_path),
                    sha256=dataset_hash(dataset_path),
                    entries=len(entries),
                    answerable_entries=sum(
                        1 for entry in entries if entry.category != "unanswerable"
                    ),
                    unanswerable_entries=sum(
                        1 for entry in entries if entry.category == "unanswerable"
                    ),
                ),
                corpus=CorpusInfo(
                    directory=str(corpus_dir),
                    sha256=corpus_hash(documents),
                    documents=len(documents),
                    chunks=chunk_total,
                    filenames=sorted(document.filename for document in documents),
                ),
                config=config_snapshot(settings),
                environment=EnvironmentInfo(
                    machine=platform.node(),
                    platform=platform.platform(),
                    python=platform.python_version(),
                    processor=platform.processor() or platform.machine(),
                ),
                judge=judge_mode,
                generation_mode=generation_mode,
                metrics=measured,
                not_measured=not_measured,
                degradation_reasons=degradation_histogram(degradation_reasons),
                per_question=per_question,
            )
        finally:
            if not keep_tenant:
                await _delete_tenant(settings, tenant_id)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------
def format_summary(report: EvalReport) -> str:
    """Render a compact, human-readable summary table for stdout."""
    dataset_line = (
        f"{report.dataset.entries} entries "
        f"({report.dataset.answerable_entries} answerable, "
        f"{report.dataset.unanswerable_entries} unanswerable), "
        f"sha256 {report.dataset.sha256[:12]}"
    )
    corpus_line = (
        f"{report.corpus.documents} documents, {report.corpus.chunks} chunks, "
        f"sha256 {report.corpus.sha256[:12]}"
    )
    lines = [
        f"CourseLLM {report.kind} evaluation",
        f"  run_id              {report.run_id}",
        f"  dataset             {dataset_line}",
        f"  corpus              {corpus_line}",
        f"  config version      {report.config.retrieval_config_version}",
        f"  embedder            {report.config.embedding_provider}/"
        f"{report.config.embedding_model} (dim {report.config.embedding_dim})",
        f"  reranker            {report.config.reranker}",
        f"  judge               {report.judge}",
        f"  generation mode     {report.generation_mode}",
        f"  git sha             {report.git_sha or 'unavailable'}",
        "",
        "  measured metrics",
    ]
    if report.metrics:
        width = max(len(key) for key in report.metrics)
        lines.extend(
            f"    {key.ljust(width)}  {value:.6f}" for key, value in sorted(report.metrics.items())
        )
    else:  # pragma: no cover - a run always measures something
        lines.append("    (none)")

    lines.append("")
    lines.append("  not measured (with reason)")
    if report.not_measured:
        width = max(len(key) for key in report.not_measured)
        lines.extend(
            f"    {key.ljust(width)}  {reason}"
            for key, reason in sorted(report.not_measured.items())
        )
    else:
        lines.append("    (none)")

    if report.degradation_reasons:
        lines.append("")
        lines.append("  degradation reasons")
        lines.extend(
            f"    {reason}: {count}" for reason, count in sorted(report.degradation_reasons.items())
        )
    return "\n".join(lines)


def write_report(report: EvalReport, output: Path) -> None:
    """Write the report as pretty, key-sorted JSON."""
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True)
    output.write_text(payload + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.runners.run_retrieval_eval",
        description="Run the LLM-free retrieval evaluation and write a report artefact.",
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="Where to write the JSON report."
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Golden dataset path (default: EVAL_DATASET_PATH or the configured default).",
    )
    parser.add_argument(
        "--corpus",
        default=None,
        help="Corpus directory (default: the 'corpus' sibling of the dataset).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N golden entries (default: EVAL_SAMPLE_LIMIT).",
    )
    parser.add_argument(
        "--keep-tenant",
        action="store_true",
        help="Leave the throwaway tenant in the database for inspection.",
    )
    return parser


async def _run(args: argparse.Namespace) -> EvalReport:
    base = Settings()
    settings = eval_settings(base)
    dataset_path = resolve_dataset_path(base, args.dataset)
    corpus_dir = Path(args.corpus) if args.corpus is not None else dataset_path.parent / "corpus"
    limit = args.limit if args.limit is not None else base.eval_sample_limit
    try:
        return await run_retrieval_eval(
            settings,
            dataset_path=dataset_path,
            corpus_dir=corpus_dir,
            limit=limit,
            keep_tenant=args.keep_tenant,
        )
    finally:
        # Dispose on the event loop that created the pool. Disposing after
        # ``asyncio.run`` returns would close connections on a dead loop.
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns non-zero on a harness error, never on a metric value."""
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:  # boundary: report and exit non-zero
        print(f"retrieval evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    write_report(report, args.output)
    print(format_summary(report))
    print(f"\nreport written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
