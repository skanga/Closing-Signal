"""Lookahead-safe execution, costs, and walk-forward contracts."""

from datetime import date
from decimal import Decimal
from typing import ClassVar, cast

from closing_signal.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
    ExecutionConvention,
    FeeModel,
)
from closing_signal.backtest.experiment import MetricSelector, WalkForwardExperiment
from closing_signal.backtest.walk_forward import WalkForwardConfig, split_walk_forward
from closing_signal.strategy.framework import PointInTimeDataView, SignalBar, StrategySelection


def _bar(day: int, open_price: str, close: str) -> SignalBar:
    open_value = Decimal(open_price)
    close_value = Decimal(close)
    return SignalBar(
        symbol="TEST",
        session_date=date(2026, 1, day),
        open=open_value,
        high=max(open_value, close_value) + 1,
        low=min(open_value, close_value) - 1,
        close=close_value,
        volume=1000,
        dollar_volume=close_value * 1000,
    )


class FirstSessionStrategy:
    strategy_id = "first_session"
    version = "1"
    parameters: ClassVar[dict[str, object]] = {"signal": "first_only"}

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]:
        if view.cutoff_session == date(2026, 1, 1):
            return [StrategySelection("TEST", 1, ("test_signal",), {"score": Decimal(1)})]
        return []


class NamedStrategy:
    version = "1"
    parameters: ClassVar[dict[str, object]] = {}

    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]:
        del view
        return []


class RecordingEngine:
    """Deterministic test double that records which data segment was evaluated."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date, date]] = []

    def run(
        self,
        strategy: NamedStrategy,
        bars_by_symbol: object,
        universe_snapshots: object,
        config: BacktestConfig,
        cash_dividends: object = None,
    ) -> BacktestResult:
        del bars_by_symbol, universe_snapshots, cash_dividends
        self.calls.append(
            (strategy.strategy_id, config.evaluation_segment, config.start, config.end)
        )
        validation_scores = {"candidate-a": Decimal("0.1"), "candidate-b": Decimal("0.2")}
        score = validation_scores[strategy.strategy_id]
        metrics = BacktestMetrics(
            total_return=score,
            annualized_return=score,
            annualized_volatility=Decimal(0),
            sharpe_ratio=score,
            sortino_ratio=score,
            maximum_drawdown=Decimal(0),
            calmar_ratio=score,
            hit_rate=Decimal(0),
            exposure=Decimal(0),
            turnover=Decimal(0),
            trade_count=0,
            average_holding_period=Decimal(0),
            benchmark_return=Decimal(0),
            benchmark_relative_return=score,
        )
        return BacktestResult(
            config=config,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            trades=(),
            positions=(),
            equity_curve=(),
            metrics=metrics,
            warnings=(),
            manifest={},
        )


def _config(execution: ExecutionConvention) -> BacktestConfig:
    return BacktestConfig(
        start=date(2026, 1, 1),
        end=date(2026, 1, 4),
        benchmark_symbol="BENCH",
        execution=execution,
        initial_capital=Decimal("10000"),
        position_size_fraction=Decimal("0.5"),
        holding_sessions=2,
        fixed_fee=Decimal("1"),
        per_share_fee=Decimal("0.01"),
        percentage_fee=Decimal("0"),
        minimum_fee=Decimal("1"),
        slippage_bps=Decimal("0"),
        annual_risk_free_rate=Decimal("0"),
        random_seed=7,
        evaluation_segment="out_of_sample",
        rebalance_rule="daily",
        holding_rule="fixed_sessions",
        position_sizing="fraction_of_initial_capital",
        missing_exit_policy="fail",
        strategy_config_version="test-v1",
        universe_version="snapshot-test",
        data_version="fixture-v1",
        code_version="test",
    )


def test_signal_executes_at_next_session_open_and_includes_fees() -> None:
    bars = {
        "TEST": [
            _bar(1, "10", "10"),
            _bar(2, "20", "21"),
            _bar(3, "22", "23"),
            _bar(4, "30", "31"),
        ],
        "BENCH": [_bar(day, "10", str(9 + day)) for day in range(1, 5)],
    }
    snapshots = {date(2026, 1, day): frozenset({"TEST"}) for day in range(1, 5)}

    result = BacktestEngine().run(
        FirstSessionStrategy(), bars, snapshots, _config(ExecutionConvention.NEXT_OPEN)
    )

    trade = result.trades[0]
    assert trade.signal_date == date(2026, 1, 1)
    assert trade.entry_date == date(2026, 1, 2)
    assert trade.entry_price == Decimal("20")
    assert trade.exit_date == date(2026, 1, 4)
    assert trade.exit_price == Decimal("30")
    assert trade.total_fees > 0
    assert result.metrics.trade_count == 1


def test_next_close_uses_next_session_close_not_signal_close() -> None:
    bars = {
        "TEST": [_bar(1, "10", "11"), _bar(2, "20", "25"), _bar(3, "24", "26")],
        "BENCH": [_bar(day, "10", "10") for day in range(1, 4)],
    }
    snapshots = {date(2026, 1, day): frozenset({"TEST"}) for day in range(1, 4)}
    config = _config(ExecutionConvention.NEXT_CLOSE).model_copy(
        update={"end": date(2026, 1, 3), "holding_sessions": 1}
    )

    result = BacktestEngine().run(FirstSessionStrategy(), bars, snapshots, config)

    assert result.trades[0].entry_price == Decimal("25")
    assert result.trades[0].entry_date == date(2026, 1, 2)


def test_fee_model_supports_all_configured_components() -> None:
    model = FeeModel(
        fixed=Decimal("1"),
        per_share=Decimal("0.01"),
        percentage=Decimal("0.001"),
        minimum=Decimal("2"),
    )

    assert model.calculate(shares=100, price=Decimal("10")) == Decimal("3.000")
    assert model.calculate(shares=1, price=Decimal("1")) == Decimal("2")


def test_walk_forward_windows_keep_test_periods_separate() -> None:
    sessions = [date(2026, 1, day) for day in range(1, 11)]
    windows = split_walk_forward(
        sessions,
        WalkForwardConfig(
            train_sessions=4,
            validation_sessions=2,
            test_sessions=2,
            step_sessions=2,
            mode="rolling",
        ),
    )

    assert windows[0].train == tuple(sessions[0:4])
    assert windows[0].validation == tuple(sessions[4:6])
    assert windows[0].test == tuple(sessions[6:8])
    assert windows[1].test == tuple(sessions[8:10])
    assert set(windows[0].test).isdisjoint(windows[0].train + windows[0].validation)


def test_anchored_walk_forward_expands_training_history() -> None:
    sessions = [date(2026, 1, day) for day in range(1, 11)]

    windows = split_walk_forward(
        sessions,
        WalkForwardConfig(
            train_sessions=4,
            validation_sessions=1,
            test_sessions=1,
            step_sessions=2,
            mode="anchored",
        ),
    )

    assert windows[0].train == tuple(sessions[:4])
    assert windows[1].train == tuple(sessions[:6])
    assert windows[1].test == (sessions[7],)


def test_walk_forward_experiment_selects_without_exposing_test_period() -> None:
    sessions = [date(2026, 1, day) for day in range(1, 9)]
    engine = RecordingEngine()
    experiment = WalkForwardExperiment(
        engine=cast(BacktestEngine, engine),
        selector=MetricSelector(metric="total_return", direction="maximize"),
    )

    result = experiment.run(
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

    assert result.folds[0].selected_candidate_index == 1
    assert result.folds[0].out_of_sample.strategy_id == "candidate-b"
    assert [call[1] for call in engine.calls] == [
        "in_sample",
        "validation",
        "in_sample",
        "validation",
        "out_of_sample",
    ]
    assert engine.calls[-1][2:] == (sessions[5], sessions[6])


def test_walk_forward_experiment_requires_complete_windows() -> None:
    experiment = WalkForwardExperiment(
        engine=BacktestEngine(),
        selector=MetricSelector(metric="sharpe_ratio", direction="maximize"),
    )

    try:
        experiment.run(
            candidates=(NamedStrategy("candidate-a"),),
            sessions=[date(2026, 1, 1)],
            bars_by_symbol={},
            universe_snapshots={},
            base_config=_config(ExecutionConvention.NEXT_OPEN),
            walk_forward=WalkForwardConfig(
                train_sessions=2,
                validation_sessions=2,
                test_sessions=2,
                step_sessions=1,
                mode="rolling",
            ),
        )
    except ValueError as exc:
        assert str(exc) == "walk-forward configuration produced no complete windows"
    else:
        raise AssertionError("expected an incomplete walk-forward request to fail")


def test_identical_backtest_inputs_produce_identical_manifests() -> None:
    bars = {
        "TEST": [_bar(day, "10", str(10 + day)) for day in range(1, 5)],
        "BENCH": [_bar(day, "10", "10") for day in range(1, 5)],
    }
    snapshots = {date(2026, 1, day): frozenset({"TEST"}) for day in range(1, 5)}
    engine = BacktestEngine()

    first = engine.run(
        FirstSessionStrategy(),
        bars,
        snapshots,
        _config(ExecutionConvention.NEXT_OPEN),
    )
    second = engine.run(
        FirstSessionStrategy(),
        bars,
        snapshots,
        _config(ExecutionConvention.NEXT_OPEN),
    )

    assert first.manifest == second.manifest


def test_backtest_reports_progress_and_allows_cancellation() -> None:
    bars = {
        "TEST": [_bar(day, "10", "10") for day in range(1, 5)],
        "BENCH": [_bar(day, "10", "10") for day in range(1, 5)],
    }
    snapshots = {date(2026, 1, day): frozenset({"TEST"}) for day in range(1, 5)}
    progress = []

    BacktestEngine().run(
        FirstSessionStrategy(),
        bars,
        snapshots,
        _config(ExecutionConvention.NEXT_OPEN),
        progress=progress.append,
    )

    assert [event.completed_sessions for event in progress] == [1, 2, 3, 4]
    assert all(event.total_sessions == 4 for event in progress)

    def cancel(event) -> None:
        del event
        raise KeyboardInterrupt

    try:
        BacktestEngine().run(
            FirstSessionStrategy(),
            bars,
            snapshots,
            _config(ExecutionConvention.NEXT_OPEN),
            progress=cancel,
        )
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("backtest cancellation did not propagate")
