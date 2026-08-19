"""Atomic-ish export of complete machine and human backtest artifacts."""

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from closing_signal.backtest.engine import BacktestResult
from closing_signal.backtest.experiment import WalkForwardExperimentResult


def write_backtest_artifacts(
    result: BacktestResult, output_directory: str | Path
) -> dict[str, Path]:
    """Write the complete reproducibility bundle and return each path."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output / "manifest.json",
        "trades": output / "trades.csv",
        "positions": output / "positions.csv",
        "equity_curve": output / "equity_curve.csv",
        "metrics": output / "metrics.json",
        "warnings": output / "warnings.json",
        "failures": output / "failures.json",
        "report": output / "report.md",
    }
    _write_json(paths["manifest"], result.manifest)
    _write_csv(paths["trades"], [asdict(item) for item in result.trades])
    _write_csv(paths["positions"], [asdict(item) for item in result.positions])
    _write_csv(paths["equity_curve"], [asdict(item) for item in result.equity_curve])
    _write_json(paths["metrics"], asdict(result.metrics))
    _write_json(paths["warnings"], list(result.warnings))
    _write_json(paths["failures"], [])
    paths["report"].write_text(_markdown_report(result), encoding="utf-8")
    return paths


def write_walk_forward_artifacts(
    result: WalkForwardExperimentResult, output_directory: str | Path
) -> dict[str, Path]:
    """Write isolated candidate/fold bundles and a reproducible selection manifest."""
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "manifest": output / "experiment-manifest.json",
        "report": output / "experiment-report.md",
    }
    fold_manifests: list[dict[str, object]] = []
    for fold_number, fold in enumerate(result.folds, start=1):
        fold_directory = output / f"fold-{fold_number:03d}"
        candidate_manifests: list[dict[str, object]] = []
        for evaluation in fold.candidates:
            candidate_directory = fold_directory / f"candidate-{evaluation.candidate_index + 1:03d}"
            write_backtest_artifacts(evaluation.training, candidate_directory / "training")
            write_backtest_artifacts(evaluation.validation, candidate_directory / "validation")
            candidate_manifests.append(
                {
                    "candidate_index": evaluation.candidate_index,
                    "strategy_id": evaluation.training.strategy_id,
                    "strategy_version": evaluation.training.strategy_version,
                    "parameters": evaluation.training.manifest.get("strategy_parameters", {}),
                    "training_metrics": asdict(evaluation.training.metrics),
                    "validation_metrics": asdict(evaluation.validation.metrics),
                }
            )
        write_backtest_artifacts(fold.out_of_sample, fold_directory / "out-of-sample")
        fold_manifests.append(
            {
                "fold": fold_number,
                "window": {
                    "training": _date_range(fold.window.train),
                    "validation": _date_range(fold.window.validation),
                    "test": _date_range(fold.window.test),
                },
                "selected_candidate_index": fold.selected_candidate_index,
                "candidates": candidate_manifests,
                "out_of_sample_metrics": asdict(fold.out_of_sample.metrics),
            }
        )
    manifest = {
        "schema_version": 1,
        "walk_forward": result.walk_forward.model_dump(mode="json"),
        "selection_policy": dict(result.selection_policy),
        "folds": fold_manifests,
    }
    _write_json(paths["manifest"], manifest)
    paths["report"].write_text(_walk_forward_markdown(result), encoding="utf-8")
    return paths


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = list(rows[0]) if rows else ["no_records"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _markdown_report(result: BacktestResult) -> str:
    segment = result.config.evaluation_segment.replace("_", " ").capitalize()
    metrics = asdict(result.metrics)
    lines = [
        f"# Backtest report: {result.strategy_id}",
        "",
        f"## {segment}",
        "",
        f"- Strategy version: `{result.strategy_version}`",
        f"- Period: {result.config.start.isoformat()} through {result.config.end.isoformat()}",
        f"- Benchmark: `{result.config.benchmark_symbol}`",
        f"- Execution: `{result.config.execution.value}`",
        f"- Risk-free rate: `{result.config.annual_risk_free_rate}`",
        f"- Trades: {len(result.trades)}",
        "",
        "## Metrics",
        "",
    ]
    lines.extend(f"- {key.replace('_', ' ').title()}: `{value}`" for key, value in metrics.items())
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in result.warnings)
    if not result.warnings:
        lines.append("- None")
    lines.extend(
        [
            "",
            "This report contains only the explicitly labeled evaluation segment. ",
            "Training, validation, and out-of-sample artifacts must remain in separate run directories.",
            "",
        ]
    )
    return "\n".join(lines)


def _date_range(sessions: tuple[date, ...]) -> dict[str, object]:
    return {
        "start": sessions[0],
        "end": sessions[-1],
        "sessions": len(sessions),
    }


def _walk_forward_markdown(result: WalkForwardExperimentResult) -> str:
    lines = [
        "# Walk-forward experiment report",
        "",
        f"Selection policy: `{json.dumps(result.selection_policy, sort_keys=True)}`",
        "",
        "| Fold | Train | Validation | Test | Selected candidate | OOS return |",
        "|---:|---|---|---|---:|---:|",
    ]
    for number, fold in enumerate(result.folds, start=1):
        lines.append(
            "| "
            f"{number} | {_range_label(fold.window.train)} | "
            f"{_range_label(fold.window.validation)} | {_range_label(fold.window.test)} | "
            f"{fold.selected_candidate_index} | "
            f"{fold.out_of_sample.metrics.total_return} |"
        )
    lines.extend(
        [
            "",
            "Candidate training and validation artifacts are isolated from the selected "
            "candidate's out-of-sample artifacts in each fold directory.",
            "",
        ]
    )
    return "\n".join(lines)


def _range_label(sessions: tuple[date, ...]) -> str:
    return f"{sessions[0]} - {sessions[-1]} ({len(sessions)})"
