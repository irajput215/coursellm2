"""Regression gate: compare a current report to the committed baseline.

The gate exists to answer one question: did a metric move by more than the
run-to-run noise justifies? Everything else is bookkeeping.

Three rules make the answer trustworthy:

1. **The tolerance table lives here and nowhere else.** Every gated metric has
   one documented slack, expressed as ``absolute + relative * |baseline|``.
   Tolerances were derived from the observed spread of repeated real runs (see
   ``evals/reports/README.md``); they are not arbitrary round numbers.
2. **``not_measured`` is skipped, not passed and not failed.** A metric the
   harness could not measure is reported as skipped with its reason. Treating it
   as a pass would hide a missing measurement; treating it as a failure would
   make an unmeasurable metric block a release.
3. **Reports from different configurations or datasets are refused.** Comparing
   across a configuration change is exactly the stale-cache-versus-real-change
   confusion ADR-0010 exists to prevent. ``--allow-config-change`` is an
   explicit, auditable override.

Exit codes: ``0`` clean, ``1`` a breach or a missing metric, ``2`` a refused
comparison (config or dataset mismatch).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Direction = Literal["higher_is_better", "lower_is_better"]


@dataclass(frozen=True, slots=True)
class Tolerance:
    """The slack allowed around a baseline value before it is a regression.

    The permitted deviation is ``absolute + relative * |baseline|``. For a
    higher-is-better metric the current value must not fall below
    ``baseline - deviation``; for a lower-is-better metric it must not rise
    above ``baseline + deviation``.
    """

    direction: Direction
    absolute: float = 0.0
    relative: float = 0.0
    note: str = ""


def _retrieval(direction: Direction = "higher_is_better", note: str = "") -> Tolerance:
    # Retrieval metrics are deterministic for fixed inputs (the hashing
    # embedder, the lexical reranker and a count-based row-id sequence, so the
    # final tie-break is stable). The observed spread across three runs was
    # exactly 0.0; the 1e-9 absolute allowance only absorbs a JSON float
    # round-trip, and is still "at or above" the measured spread.
    return Tolerance(direction=direction, absolute=1e-9, relative=0.0, note=note)


def _latency() -> Tolerance:
    # Measured spread across three real runs is recorded in
    # evals/reports/README.md. The largest single metric spread observed was
    # 81.82 ms (lexical p99). Wall-clock latency on a shared machine is far
    # noisier than any retrieval metric, so the allowance is a flat 100 ms
    # plus the baseline value itself, which is above the largest observed
    # spread while still catching an order-of-magnitude regression.
    return Tolerance(
        direction="lower_is_better",
        absolute=100.0,
        relative=1.0,
        note="wall-clock latency; tolerance derived from the measured run-to-run spread",
    )


#: The one tolerance table. Keys are the dotted metric paths emitted by the
#: runners. Latency is non-deterministic by nature, so it carries a wide
#: relative tolerance derived from the observed spread; the retrieval and
#: citation metrics are deterministic and are gated at zero slack.
TOLERANCES: dict[str, Tolerance] = {
    "retrieval.semantic_recall_at_20": _retrieval(),
    "retrieval.lexical_recall_at_20": _retrieval(),
    "retrieval.fused_recall_at_10": _retrieval(),
    "retrieval.fused_mrr": _retrieval(),
    "retrieval.precision_at_5": _retrieval(),
    "retrieval.recall_at_5": _retrieval(),
    "retrieval.mrr_at_5": _retrieval(),
    "retrieval.ndcg_at_5": _retrieval(),
    "retrieval.context_precision": _retrieval(),
    "retrieval.context_recall": _retrieval(),
    "citations.precision": _retrieval(note="deterministic for a fixed generator output"),
    "citations.recall": _retrieval(note="deterministic for a fixed generator output"),
    "citations.hallucination_rate": Tolerance(
        direction="lower_is_better",
        absolute=1e-9,
        relative=0.0,
        note="deterministic for a fixed generator output",
    ),
    "generation.faithfulness": Tolerance(
        direction="higher_is_better",
        absolute=0.05,
        relative=0.0,
        note="LLM judging is stochastic; small drifts are expected",
    ),
    "generation.answer_relevance": Tolerance(
        direction="higher_is_better",
        absolute=0.05,
        relative=0.0,
        note="LLM judging is stochastic; small drifts are expected",
    ),
    "generation.answer_correctness": Tolerance(
        direction="higher_is_better",
        absolute=0.05,
        relative=0.0,
        note="LLM judging is stochastic; small drifts are expected",
    ),
    "system.degradation_rate": Tolerance(
        direction="lower_is_better",
        absolute=1e-9,
        relative=0.0,
        note="a new degradation reason is a real change",
    ),
    "system.error_rate": Tolerance(direction="lower_is_better", absolute=1e-9, relative=0.0),
    "system.generation_degradation_rate": Tolerance(
        direction="lower_is_better",
        absolute=1e-9,
        relative=0.0,
        note="recorded by the full RAG run; absent from the retrieval-only baseline",
    ),
    "system.semantic_p50_ms": _latency(),
    "system.semantic_p95_ms": _latency(),
    "system.semantic_p99_ms": _latency(),
    "system.lexical_p50_ms": _latency(),
    "system.lexical_p95_ms": _latency(),
    "system.lexical_p99_ms": _latency(),
    "system.fusion_p50_ms": _latency(),
    "system.fusion_p95_ms": _latency(),
    "system.fusion_p99_ms": _latency(),
    "system.rerank_p50_ms": _latency(),
    "system.rerank_p95_ms": _latency(),
    "system.rerank_p99_ms": _latency(),
}


@dataclass(frozen=True, slots=True)
class Breach:
    """One metric that moved outside its tolerance."""

    metric: str
    baseline: float
    current: float
    allowed: float
    direction: Direction

    def describe(self) -> str:
        sign = "below" if self.direction == "higher_is_better" else "above"
        comparison = "<" if self.direction == "higher_is_better" else ">"
        return (
            f"{self.metric}: current {self.current:.6f} is {sign} the permitted "
            f"{self.allowed:.6f} (baseline {self.baseline:.6f}; breach if current "
            f"{comparison} {self.allowed:.6f})"
        )


@dataclass
class RegressionResult:
    """The outcome of one comparison, including everything that was skipped."""

    ok: bool
    breaches: list[Breach] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    refusal: str | None = None
    compared: int = 0


def load_report(path: Path) -> dict[str, Any]:
    """Load a report JSON object, failing loudly if it is not an object."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise ValueError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"{path} must contain a JSON object"
        raise ValueError(msg)
    return payload


def _config_version(report: dict[str, Any]) -> str | None:
    config = report.get("config")
    if isinstance(config, dict):
        value = config.get("retrieval_config_version")
        if isinstance(value, str):
            return value
    return None


def _dataset_hash(report: dict[str, Any]) -> str | None:
    dataset = report.get("dataset")
    if isinstance(dataset, dict):
        value = dataset.get("sha256")
        if isinstance(value, str):
            return value
    return None


def _metrics(report: dict[str, Any]) -> dict[str, float]:
    raw = report.get("metrics")
    if not isinstance(raw, dict):
        return {}
    typed: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            msg = f"metric {key!r} is not numeric: {value!r}"
            raise ValueError(msg)
        typed[str(key)] = float(value)
    return typed


def _not_measured(report: dict[str, Any]) -> dict[str, str]:
    raw = report.get("not_measured")
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _deviation(tolerance: Tolerance, baseline: float) -> float:
    return tolerance.absolute + tolerance.relative * abs(baseline)


def _breach(tolerance: Tolerance, baseline: float, current: float) -> bool:
    if tolerance.direction == "higher_is_better":
        return current < baseline - _deviation(tolerance, baseline)
    return current > baseline + _deviation(tolerance, baseline)


def compare_reports(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    tolerances: dict[str, Tolerance] | None = None,
    allow_config_change: bool = False,
) -> RegressionResult:
    """Compare ``current`` against ``baseline`` and describe every difference."""
    table = TOLERANCES if tolerances is None else tolerances
    result = RegressionResult(ok=True)

    baseline_config = _config_version(baseline)
    current_config = _config_version(current)
    if baseline_config != current_config and not allow_config_change:
        result.ok = False
        result.refusal = (
            "retrieval_config_version differs "
            f"(baseline {baseline_config!r}, current {current_config!r}); comparing across a "
            "configuration change is refused. Pass --allow-config-change to override."
        )
        return result

    baseline_dataset = _dataset_hash(baseline)
    current_dataset = _dataset_hash(current)
    if baseline_dataset != current_dataset and not allow_config_change:
        result.ok = False
        result.refusal = (
            "dataset hash differs "
            f"(baseline {baseline_dataset!r}, current {current_dataset!r}); comparing across a "
            "dataset change is refused. Pass --allow-config-change to override."
        )
        return result

    baseline_metrics = _metrics(baseline)
    current_metrics = _metrics(current)
    current_not_measured = _not_measured(current)

    for metric, baseline_value in sorted(baseline_metrics.items()):
        if metric in current_not_measured:
            result.skipped[metric] = current_not_measured[metric]
            continue
        if metric not in current_metrics:
            result.missing.append(metric)
            result.ok = False
            continue
        tolerance = table.get(metric)
        if tolerance is None:
            result.unknown.append(metric)
            result.ok = False
            continue
        current_value = current_metrics[metric]
        result.compared += 1
        if _breach(tolerance, baseline_value, current_value):
            result.breaches.append(
                Breach(
                    metric=metric,
                    baseline=baseline_value,
                    current=current_value,
                    allowed=(
                        baseline_value - _deviation(tolerance, baseline_value)
                        if tolerance.direction == "higher_is_better"
                        else baseline_value + _deviation(tolerance, baseline_value)
                    ),
                    direction=tolerance.direction,
                )
            )
            result.ok = False

    return result


def format_result(result: RegressionResult) -> str:
    """Render a summary of the comparison for stdout."""
    lines: list[str] = []
    if result.refusal is not None:
        lines.append(f"REFUSED: {result.refusal}")
        return "\n".join(lines)

    lines.append(f"compared {result.compared} metric(s)")
    if result.skipped:
        lines.append(f"skipped {len(result.skipped)} not_measured metric(s):")
        lines.extend(f"  {metric}: {reason}" for metric, reason in sorted(result.skipped.items()))
    if result.missing:
        lines.append(f"missing {len(result.missing)} metric(s) in the current report:")
        lines.extend(f"  {metric}" for metric in sorted(result.missing))
    if result.unknown:
        lines.append(f"no tolerance defined for {len(result.unknown)} metric(s):")
        lines.extend(f"  {metric}" for metric in sorted(result.unknown))
    if result.breaches:
        lines.append(f"{len(result.breaches)} regression(s):")
        lines.extend(f"  {breach.describe()}" for breach in result.breaches)
    if result.ok:
        lines.append("no regressions")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.runners.check_regression",
        description="Fail when a metric in the current report breaches its baseline tolerance.",
    )
    parser.add_argument("--baseline", required=True, type=Path, help="Committed baseline report.")
    parser.add_argument("--current", required=True, type=Path, help="Report to compare.")
    parser.add_argument(
        "--allow-config-change",
        action="store_true",
        help="Compare even when the config version or dataset hash differs.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point. 0 clean, 1 breach or missing metric, 2 refused comparison."""
    args = build_parser().parse_args(argv)
    try:
        baseline = load_report(args.baseline)
        current = load_report(args.current)
    except (OSError, ValueError) as exc:
        print(f"could not read a report: {exc}", file=sys.stderr)
        return 2
    result = compare_reports(
        baseline,
        current,
        allow_config_change=args.allow_config_change,
    )
    print(format_result(result))
    if result.refusal is not None:
        return 2
    return 0 if result.ok else 1


__all__ = [
    "TOLERANCES",
    "Breach",
    "RegressionResult",
    "Tolerance",
    "build_parser",
    "compare_reports",
    "format_result",
    "load_report",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
