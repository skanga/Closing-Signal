"""Machine- and human-readable backtest artifact contracts."""

import json
from datetime import date
from typing import cast

from closing_signal.backtest.engine import BacktestEngine, ExecutionConvention
from closing_signal.backtest.experiment import MetricSelector, WalkForwardExperiment
from closing_signal.backtest.reporting import (
    write_backtest_artifacts,
    write_walk_forward_artifacts,
)
from closing_signal.backtest.walk_forward import WalkForwardConfig
from tests.test_backtest import (
    FirstSessionStrategy,
    NamedStrategy,
    RecordingEngine,
    _bar,
    _config,
)


def test_report_bundle_contains_manifest_trades_positions_curve_metrics_and_failures(
    tmp_path,
) -> None:
    bars = {
        "TEST": [_bar(1, "10", "10"), _bar(2, "11", "12"), _bar(3, "13", "14")],
        "BENCH": [_bar(day, "10", "10") for day in range(1, 4)],
    }
    snapshots = {date(2026, 1, day): frozenset({"TEST"}) for day in range(1, 4)}
    config = _config(ExecutionConvention.NEXT_OPEN).model_copy(
        update={"end": date(2026, 1, 3), "holding_sessions": 1}
    )
    result = BacktestEngine().run(FirstSessionStrategy(), bars, snapshots, config)

    paths = write_backtest_artifacts(result, tmp_path / "report")

    assert set(paths) == {
        "manifest",
        "trades",
        "positions",
        "equity_curve",
        "metrics",
        "warnings",
        "failures",
        "report",
    }
    assert (
        json.loads(paths["manifest"].read_text(encoding="utf-8"))["strategy_id"] == "first_session"
    )
    assert "Out of sample" in paths["report"].read_text(encoding="utf-8")


def test_walk_forward_bundle_separates_selection_and_out_of_sample_artifacts(
    tmp_path,
) -> None:
    sessions = [date(2026, 1, day) for day in range(1, 8)]
    result = WalkForwardExperiment(
        engine=cast(BacktestEngine, RecordingEngine()),
        selector=MetricSelector(metric="total_return", direction="maximize"),
    ).run(
        candidates=(NamedStrategy("candidate-a"), NamedStrategy("candidate-b")),
        sessions=sessions,
        bars_by_symbol={},
        universe_snapshots={},
        base_config=_config(ExecutionConvention.NEXT_OPEN),
        walk_forward=WalkForwardConfig(
            train_sessions=3,
            validation_sessions=2,
            test_sessions=2,
            step_sessions=2,
            mode="rolling",
        ),
    )

    paths = write_walk_forward_artifacts(result, tmp_path / "walk-forward")
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    assert manifest["selection_policy"] == {
        "direction": "maximize",
        "metric": "total_return",
    }
    assert manifest["folds"][0]["selected_candidate_index"] == 1
    assert (
        tmp_path / "walk-forward" / "fold-001" / "candidate-001" / "training" / "metrics.json"
    ).exists()
    assert (
        tmp_path / "walk-forward" / "fold-001" / "candidate-002" / "validation" / "metrics.json"
    ).exists()
    assert (tmp_path / "walk-forward" / "fold-001" / "out-of-sample" / "metrics.json").exists()
