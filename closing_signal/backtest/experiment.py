"""Leakage-safe orchestration for explicit walk-forward experiments."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from closing_signal.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestProgress,
    BacktestResult,
)
from closing_signal.backtest.walk_forward import (
    WalkForwardConfig,
    WalkForwardWindow,
    split_walk_forward,
)
from closing_signal.strategy.framework import SignalBar, Strategy

type SelectionMetric = Literal[
    "total_return",
    "annualized_return",
    "sharpe_ratio",
    "sortino_ratio",
    "maximum_drawdown",
    "calmar_ratio",
    "hit_rate",
    "benchmark_relative_return",
]


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Selection evidence that deliberately contains no test-period result."""

    candidate_index: int
    training: BacktestResult
    validation: BacktestResult


class CandidateSelector(Protocol):
    """Select a candidate using only in-sample and validation evidence."""

    @property
    def selection_policy(self) -> Mapping[str, object]: ...

    def select(self, evaluations: Sequence[CandidateEvaluation]) -> int: ...


class MetricSelector(BaseModel):
    """Choose the best validation metric using an explicit direction."""

    metric: SelectionMetric
    direction: Literal["maximize", "minimize"]
    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def selection_policy(self) -> Mapping[str, object]:
        return self.model_dump(mode="json")

    def select(self, evaluations: Sequence[CandidateEvaluation]) -> int:
        if not evaluations:
            raise ValueError("candidate evaluations cannot be empty")
        scored = [
            (
                getattr(evaluation.validation.metrics, self.metric),
                -evaluation.candidate_index,
                evaluation.candidate_index,
            )
            for evaluation in evaluations
        ]
        if self.direction == "maximize":
            return max(scored)[2]
        return min((score, -tie_breaker, index) for score, tie_breaker, index in scored)[2]


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """Candidate evidence, selection, and untouched out-of-sample result for one fold."""

    window: WalkForwardWindow
    candidates: tuple[CandidateEvaluation, ...]
    selected_candidate_index: int
    out_of_sample: BacktestResult


@dataclass(frozen=True, slots=True)
class WalkForwardExperimentResult:
    """All chronological folds from a reproducible experiment."""

    walk_forward: WalkForwardConfig
    selection_policy: Mapping[str, object]
    folds: tuple[WalkForwardFold, ...]


class WalkForwardExperiment:
    """Run candidate selection before evaluating each untouched test segment."""

    def __init__(
        self,
        *,
        engine: BacktestEngine,
        selector: CandidateSelector,
        progress: Callable[[BacktestProgress], None] | None = None,
    ) -> None:
        self.engine = engine
        self.selector = selector
        self.progress = progress

    def run(
        self,
        *,
        candidates: Sequence[Strategy],
        sessions: list[date],
        bars_by_symbol: Mapping[str, Sequence[SignalBar]],
        universe_snapshots: Mapping[date, frozenset[str]],
        base_config: BacktestConfig,
        walk_forward: WalkForwardConfig,
        cash_dividends: Mapping[tuple[str, date], Decimal] | None = None,
    ) -> WalkForwardExperimentResult:
        if not candidates:
            raise ValueError("at least one strategy candidate is required")
        windows = split_walk_forward(sessions, walk_forward)
        if not windows:
            raise ValueError("walk-forward configuration produced no complete windows")

        folds: list[WalkForwardFold] = []
        for window in windows:
            evaluations = tuple(
                CandidateEvaluation(
                    candidate_index=index,
                    training=self._run_segment(
                        candidate,
                        window.train,
                        "in_sample",
                        bars_by_symbol,
                        universe_snapshots,
                        base_config,
                        cash_dividends,
                    ),
                    validation=self._run_segment(
                        candidate,
                        window.validation,
                        "validation",
                        bars_by_symbol,
                        universe_snapshots,
                        base_config,
                        cash_dividends,
                    ),
                )
                for index, candidate in enumerate(candidates)
            )
            selected_index = self.selector.select(evaluations)
            if not 0 <= selected_index < len(candidates):
                raise ValueError("candidate selector returned an invalid index")
            out_of_sample = self._run_segment(
                candidates[selected_index],
                window.test,
                "out_of_sample",
                bars_by_symbol,
                universe_snapshots,
                base_config,
                cash_dividends,
            )
            folds.append(WalkForwardFold(window, evaluations, selected_index, out_of_sample))
        return WalkForwardExperimentResult(
            walk_forward=walk_forward,
            selection_policy=dict(self.selector.selection_policy),
            folds=tuple(folds),
        )

    def _run_segment(
        self,
        strategy: Strategy,
        sessions: tuple[date, ...],
        segment: Literal["in_sample", "validation", "out_of_sample"],
        bars_by_symbol: Mapping[str, Sequence[SignalBar]],
        universe_snapshots: Mapping[date, frozenset[str]],
        base_config: BacktestConfig,
        cash_dividends: Mapping[tuple[str, date], Decimal] | None,
    ) -> BacktestResult:
        config = base_config.model_copy(
            update={
                "start": sessions[0],
                "end": sessions[-1],
                "evaluation_segment": segment,
            }
        )
        if self.progress is None:
            return self.engine.run(
                strategy,
                bars_by_symbol,
                universe_snapshots,
                config,
                cash_dividends,
            )
        return self.engine.run(
            strategy,
            bars_by_symbol,
            universe_snapshots,
            config,
            cash_dividends,
            progress=self.progress,
        )
