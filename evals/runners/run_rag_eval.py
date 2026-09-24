"""The full RAG evaluation: the retrieval half plus generation and judging.

The retrieval half is identical to :mod:`evals.runners.run_retrieval_eval` and
is imported rather than reimplemented, so the two reports share one loop and one
definition of every retrieval metric.

Generation needs a model. When a provider key is configured, the real
:class:`~coursellm.llm.gateway.LiteLLMGateway` and
:class:`~evals.judges.llm_judge.LLMJudge` are used and the judge-derived metrics
are measured. When no key is configured, **no model is called**: the answer is
produced by the generator's deterministic extractive fallback, the
:class:`~evals.judges.llm_judge.ScriptedJudge` is used to exercise the harness
mechanics, and every judge-derived metric is recorded as ``not_measured`` with
reason ``scripted_judge``. Citation metrics are still measured, because the
extractive fallback is a real output of the real generator that carries real
citation ids.

That distinction is the whole point of this module: a scripted judge's
token-overlap score must never appear in a report as a quality measurement.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Literal

from coursellm.core.config import Settings
from coursellm.core.errors import ServiceUnavailableError
from coursellm.db.session import dispose_engine
from coursellm.llm.cost import count_tokens
from coursellm.llm.gateway import LiteLLMGateway, LLMGateway
from coursellm.llm.types import LLMRequest, LLMResponse
from coursellm.prompts.loader import PromptLibrary
from coursellm.rag.generation.citations import is_refusal
from coursellm.rag.generation.context import ChunkPosition, DocumentMeta, assemble
from coursellm.rag.generation.generator import AnswerGenerator
from coursellm.rag.rerank.pipeline import RankingOutcome
from evals.judges.llm_judge import Judge, LLMJudge, ScriptedJudge
from evals.metrics.system import TokenUsage
from evals.runners.run_retrieval_eval import (
    EvalReport,
    GenerateFn,
    GenerationRecord,
    GoldenEntry,
    eval_settings,
    format_summary,
    resolve_dataset_path,
    run_retrieval_eval,
    write_report,
)

#: The course name shown to the generator. The evaluation corpus is not attached
#: to a real course, so the name is a constant rather than a database lookup.
_EVAL_COURSE_NAME = "CourseLLM evaluation corpus"


class NoProviderGateway:
    """A gateway that always reports the provider as unavailable.

    Injected when no provider key is configured, so the application's real
    degradation path — the extractive fallback in
    :class:`~coursellm.rag.generation.generator.AnswerGenerator` — is exercised
    without opening a network connection or pretending a model answered.
    """

    async def complete(self, request: LLMRequest) -> LLMResponse:
        raise ServiceUnavailableError(
            "No LLM provider key is configured; the evaluation runs the extractive fallback."
        )

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        # The conditional keeps this an async generator (a bare ``raise`` followed
        # by ``yield`` is unreachable code). No caller reaches it: ``complete`` is
        # the only method the generator uses.
        if request.messages:  # pragma: no cover - non-empty in practice, always raises
            raise ServiceUnavailableError(
                "No LLM provider key is configured; the evaluation runs the extractive fallback."
            )
        yield ""  # pragma: no cover


def provider_configured(settings: Settings) -> bool:
    """Whether any configured provider key is non-empty."""
    return any(
        key.strip()
        for key in (
            settings.openai_api_key,
            settings.anthropic_api_key,
            settings.gemini_api_key,
            settings.groq_api_key,
        )
    )


def _generator(settings: Settings, gateway: LLMGateway) -> AnswerGenerator:
    return AnswerGenerator(settings, gateway, PromptLibrary(settings))


def make_generate(settings: Settings, generator: AnswerGenerator) -> GenerateFn:
    """Build the generation callback injected into the shared harness loop."""

    def counter(text: str) -> int:
        return count_tokens(text, settings.reasoning_model)

    async def generate(
        entry: GoldenEntry,
        ranked: RankingOutcome,
        documents_by_id: Mapping[uuid.UUID, DocumentMeta],
        positions: Mapping[uuid.UUID, ChunkPosition],
    ) -> GenerationRecord:
        context = assemble(
            ranked.results,
            documents_by_id=documents_by_id,
            settings=settings,
            token_counter=counter,
            chunk_positions=positions,
        )
        answer = await generator.answer(entry.question, context, course_name=_EVAL_COURSE_NAME)
        expected = set(entry.expected_sources)
        offered = tuple(citation.citation_id for citation in context.citations)
        relevant_offered = tuple(
            citation.citation_id for citation in context.citations if citation.filename in expected
        )
        usages: tuple[TokenUsage, ...] = ()
        if answer.usage is not None:
            usages = (
                TokenUsage(
                    model=answer.usage.model,
                    prompt_tokens=answer.usage.prompt_tokens,
                    completion_tokens=answer.usage.completion_tokens,
                    fallback_used=answer.usage.fallback_used,
                ),
            )
        return GenerationRecord(
            answer=answer.text,
            offered=offered,
            relevant_offered=relevant_offered,
            refused=is_refusal(answer.text),
            degraded=tuple(answer.degraded),
            usages=usages,
            mode="llm" if answer.usage is not None else "extractive_fallback",
        )

    return generate


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.runners.run_rag_eval",
        description="Run the full RAG evaluation (retrieval + generation) and write a report.",
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
        "--judge",
        choices=("auto", "scripted", "llm"),
        default="auto",
        help=(
            "auto uses the real LLM judge when a provider key is configured and the "
            "scripted judge otherwise (default: auto)."
        ),
    )
    parser.add_argument(
        "--keep-tenant",
        action="store_true",
        help="Leave the throwaway tenant in the database for inspection.",
    )
    return parser


def select_judge(settings: Settings, choice: Literal["auto", "scripted", "llm"]) -> Judge:
    """Choose the judge, refusing an explicit ``llm`` choice without a provider key."""
    configured = provider_configured(settings)
    if choice == "llm" and not configured:
        msg = (
            "--judge llm was requested but no provider key is configured. Set "
            "OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY or GROQ_API_KEY, or "
            "use --judge scripted."
        )
        raise ValueError(msg)
    if choice == "scripted":
        return ScriptedJudge()
    if choice == "llm" or configured:
        return LLMJudge(LiteLLMGateway(settings))
    return ScriptedJudge()


async def run_rag_eval(
    settings: Settings,
    *,
    dataset_path: Path,
    corpus_dir: Path,
    limit: int = 0,
    judge: Judge | None = None,
    keep_tenant: bool = False,
) -> EvalReport:
    """Run the full harness, choosing the gateway according to the judge."""
    configured = provider_configured(settings)
    active_judge: Judge
    if judge is not None:
        active_judge = judge
    elif configured:
        active_judge = LLMJudge(LiteLLMGateway(settings))
    else:
        active_judge = ScriptedJudge()
    gateway: LLMGateway = LiteLLMGateway(settings) if configured else NoProviderGateway()
    generator = _generator(settings, gateway)
    generate = make_generate(settings, generator)
    return await run_retrieval_eval(
        settings,
        dataset_path=dataset_path,
        corpus_dir=corpus_dir,
        limit=limit,
        judge=active_judge,
        generate=generate,
        keep_tenant=keep_tenant,
        kind="rag",
    )


async def _run(args: argparse.Namespace) -> EvalReport:
    base = Settings()
    settings = eval_settings(base)
    dataset_path = resolve_dataset_path(base, args.dataset)
    corpus_dir = Path(args.corpus) if args.corpus is not None else dataset_path.parent / "corpus"
    limit = args.limit if args.limit is not None else base.eval_sample_limit
    judge = select_judge(settings, args.judge)
    try:
        return await run_rag_eval(
            settings,
            dataset_path=dataset_path,
            corpus_dir=corpus_dir,
            limit=limit,
            judge=judge,
            keep_tenant=args.keep_tenant,
        )
    finally:
        # Dispose on the event loop that created the pool.
        await dispose_engine()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns non-zero on a harness error, never on a metric value."""
    args = build_parser().parse_args(argv)
    try:
        report = asyncio.run(_run(args))
    except Exception as exc:
        print(f"rag evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    write_report(report, args.output)
    print(format_summary(report))
    print(f"\nreport written to {args.output}")
    return 0


__all__ = [
    "NoProviderGateway",
    "build_parser",
    "main",
    "make_generate",
    "provider_configured",
    "run_rag_eval",
    "select_judge",
]


if __name__ == "__main__":
    raise SystemExit(main())
