"""Unit tests for the regression gate.

The gate's contract is narrow and load-bearing: a metric inside its tolerance
passes, one outside fails, a metric missing from the current report fails, a
``not_measured`` metric is skipped and reported, and a configuration or dataset
change is refused unless explicitly overridden.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from evals.runners.check_regression import (
    TOLERANCES,
    Tolerance,
    compare_reports,
    main,
)

pytestmark = pytest.mark.unit

_RECALL = "retrieval.recall_at_5"
_SEMANTIC_P99 = "system.semantic_p99_ms"


def _report(
    metrics: dict[str, float],
    *,
    config: str = "cfg-1",
    dataset: str = "data-1",
    not_measured: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "config": {"retrieval_config_version": config},
        "dataset": {"sha256": dataset},
        "metrics": metrics,
        "not_measured": not_measured or {},
    }


_TOLERANCES = {
    _RECALL: Tolerance(direction="higher_is_better", absolute=0.05),
    _SEMANTIC_P99: Tolerance(direction="lower_is_better", absolute=10.0),
}


def test_metric_inside_tolerance_passes() -> None:
    baseline = _report({_RECALL: 0.90})
    current = _report({_RECALL: 0.88})
    result = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert result.ok is True
    assert result.breaches == []
    assert result.compared == 1


def test_metric_exactly_at_the_tolerance_boundary_passes() -> None:
    baseline = _report({_RECALL: 0.90})
    current = _report({_RECALL: 0.85})  # baseline - 0.05
    assert compare_reports(baseline, current, tolerances=_TOLERANCES).ok is True


def test_metric_outside_tolerance_fails() -> None:
    baseline = _report({_RECALL: 0.90})
    current = _report({_RECALL: 0.84})
    result = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert result.ok is False
    assert [breach.metric for breach in result.breaches] == [_RECALL]
    assert "below" in result.breaches[0].describe()


def test_lower_is_better_metric_outside_tolerance_fails() -> None:
    baseline = _report({_SEMANTIC_P99: 10.0})
    assert (
        compare_reports(baseline, _report({_SEMANTIC_P99: 15.0}), tolerances=_TOLERANCES).ok is True
    )
    result = compare_reports(baseline, _report({_SEMANTIC_P99: 30.0}), tolerances=_TOLERANCES)
    assert result.ok is False
    assert "above" in result.breaches[0].describe()


def test_missing_metric_in_current_report_fails() -> None:
    baseline = _report({_RECALL: 0.90})
    current = _report({})
    result = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert result.ok is False
    assert result.missing == [_RECALL]
    assert result.breaches == []


def test_not_measured_metric_is_skipped_and_reported() -> None:
    baseline = _report({_RECALL: 0.90})
    current = _report({}, not_measured={_RECALL: "scripted_judge"})
    result = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert result.ok is True
    assert result.skipped == {_RECALL: "scripted_judge"}
    assert result.missing == []
    assert result.compared == 0


def test_metric_without_a_tolerance_fails() -> None:
    baseline = _report({"retrieval.recall_at_5": 0.9, "retrieval.unregistered": 1.0})
    current = _report({"retrieval.recall_at_5": 0.9, "retrieval.unregistered": 1.0})
    result = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert result.ok is False
    assert result.unknown == ["retrieval.unregistered"]


def test_config_version_mismatch_is_refused_unless_overridden() -> None:
    baseline = _report({_RECALL: 0.90}, config="cfg-1")
    current = _report({_RECALL: 0.90}, config="cfg-2")
    refused = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert refused.ok is False
    assert refused.refusal is not None
    assert "retrieval_config_version" in refused.refusal

    overridden = compare_reports(
        baseline, current, tolerances=_TOLERANCES, allow_config_change=True
    )
    assert overridden.refusal is None
    assert overridden.ok is True


def test_dataset_hash_mismatch_is_refused_unless_overridden() -> None:
    baseline = _report({_RECALL: 0.90}, dataset="data-1")
    current = _report({_RECALL: 0.90}, dataset="data-2")
    refused = compare_reports(baseline, current, tolerances=_TOLERANCES)
    assert refused.ok is False
    assert refused.refusal is not None
    assert "dataset hash" in refused.refusal

    overridden = compare_reports(
        baseline, current, tolerances=_TOLERANCES, allow_config_change=True
    )
    assert overridden.refusal is None
    assert overridden.ok is True


def test_shipped_tolerance_table_covers_every_measured_metric_name() -> None:
    # The table is the single source of tolerances; a metric that is emitted
    # without one would fail the gate for the wrong reason.
    assert _RECALL in TOLERANCES
    assert _SEMANTIC_P99 in TOLERANCES
    assert TOLERANCES[_RECALL].absolute >= 1e-9
    assert TOLERANCES[_SEMANTIC_P99].absolute >= 100.0


def _write(path: Path, report: dict[str, Any]) -> Path:
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_cli_returns_zero_on_a_clean_comparison(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    baseline = _write(tmp_path / "baseline.json", _report({_RECALL: 0.9}))
    current = _write(tmp_path / "current.json", _report({_RECALL: 0.9}))
    assert main(["--baseline", str(baseline), "--current", str(current)]) == 0
    assert "no regressions" in capsys.readouterr().out


def test_cli_returns_one_on_a_breach(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = _write(tmp_path / "baseline.json", _report({_RECALL: 0.9}))
    current = _write(tmp_path / "current.json", _report({_RECALL: 0.1}))
    assert main(["--baseline", str(baseline), "--current", str(current)]) == 1
    assert "regression" in capsys.readouterr().out


def test_cli_returns_two_on_a_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = _write(tmp_path / "baseline.json", _report({_RECALL: 0.9}, config="cfg-1"))
    current = _write(tmp_path / "current.json", _report({_RECALL: 0.9}, config="cfg-2"))
    assert main(["--baseline", str(baseline), "--current", str(current)]) == 2
    assert "REFUSED" in capsys.readouterr().out
