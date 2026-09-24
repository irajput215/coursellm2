"""End-to-end tests for the retrieval evaluation harness against PostgreSQL.

The harness is the artefact-producing path, so these tests exercise the real
ingestion pipeline, the real retrievers and the real report schema. They are the
place the two properties the regression gate depends on are asserted:

* every golden question's expected sources are actually retrievable (the
  lower-bound sanity check that keeps the dataset honest), and
* two runs on the same inputs produce identical aggregate retrieval metrics.
"""

from __future__ import annotations

import pytest
from evals.runners.run_retrieval_eval import (
    EvalReport,
    eval_settings,
    run_retrieval_eval,
)

from coursellm.core.config import REPO_ROOT, Settings

pytestmark = pytest.mark.integration

_DATASET = REPO_ROOT / "evals" / "datasets" / "golden_rag.jsonl"
_CORPUS = REPO_ROOT / "evals" / "datasets" / "corpus"


async def _run(settings: Settings) -> EvalReport:
    return await run_retrieval_eval(
        eval_settings(settings),
        dataset_path=_DATASET,
        corpus_dir=_CORPUS,
    )


async def test_harness_runs_end_to_end_and_the_schema_validates(
    pg_settings: Settings,
) -> None:
    report = await _run(pg_settings)
    # Round-tripping through the model is the schema validation: an extra or
    # missing field raises rather than being written out silently.
    assert EvalReport.model_validate(report.model_dump()) == report

    assert report.kind == "retrieval"
    assert report.judge == "none"
    assert report.generation_mode == "none"
    assert report.dataset.entries >= 20
    assert report.dataset.unanswerable_entries >= 3
    assert report.corpus.documents >= 6
    assert report.corpus.chunks > 0
    assert report.config.embedding_provider == "hashing"
    assert report.config.reranker == "lexical-fallback"
    assert report.metrics, "the retrieval harness must measure something"


async def test_report_records_config_version_and_dataset_hash(
    pg_settings: Settings,
) -> None:
    report = await _run(pg_settings)
    assert report.config.retrieval_config_version
    assert len(report.config.retrieval_config_version) == 12
    assert len(report.dataset.sha256) == 64
    assert len(report.corpus.sha256) == 64
    assert report.dataset.sha256 == EvalReport.model_validate(report.model_dump()).dataset.sha256


async def test_every_answerable_question_has_its_expected_sources_retrievable(
    pg_settings: Settings,
) -> None:
    report = await _run(pg_settings)
    answerable = [question for question in report.per_question if question.answerable]
    assert answerable, "the dataset must contain answerable questions"

    failures: list[str] = []
    for question in answerable:
        missing = sorted(set(question.expected_sources) - set(question.candidate_documents))
        if missing:
            failures.append(f"{question.id}: {missing}")
    assert not failures, (
        f"every expected source must be retrievable by the retrieval stack; missing: {failures}"
    )


async def test_unanswerable_questions_carry_no_expected_sources(
    pg_settings: Settings,
) -> None:
    report = await _run(pg_settings)
    unanswerable = [question for question in report.per_question if not question.answerable]
    assert len(unanswerable) >= 3
    assert all(question.expected_sources == [] for question in unanswerable)
    assert all(question.metrics == {} for question in unanswerable)


async def test_generation_metrics_are_not_measured_without_a_judge(
    pg_settings: Settings,
) -> None:
    report = await _run(pg_settings)
    assert report.not_measured["generation.faithfulness"] == "no_llm"
    assert report.not_measured["generation.answer_relevance"] == "no_llm"
    assert report.not_measured["generation.answer_correctness"] == "no_llm"
    assert report.not_measured["citations.precision"] == "no_generation"
    assert report.not_measured["system.cost_usd"] == "no_llm"


async def test_two_runs_produce_identical_aggregate_retrieval_metrics(
    pg_settings: Settings,
) -> None:
    first = await _run(pg_settings)
    second = await _run(pg_settings)

    first_retrieval = {
        key: value for key, value in first.metrics.items() if key.startswith("retrieval.")
    }
    second_retrieval = {
        key: value for key, value in second.metrics.items() if key.startswith("retrieval.")
    }
    assert first_retrieval == second_retrieval
    assert first_retrieval, "no retrieval metrics were produced"

    # The per-question ranking is identical too, not merely the averages.
    first_ranking = [
        [(hit.document, hit.chunk_id) for hit in question.retrieved]
        for question in first.per_question
    ]
    second_ranking = [
        [(hit.document, hit.chunk_id) for hit in question.retrieved]
        for question in second.per_question
    ]
    assert first_ranking == second_ranking
