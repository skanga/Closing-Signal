"""Lookahead-safe strategy backtesting and walk-forward evaluation."""

from closing_signal.backtest.engine import BacktestConfig, BacktestEngine, ExecutionConvention
from closing_signal.backtest.experiment import MetricSelector, WalkForwardExperiment

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "ExecutionConvention",
    "MetricSelector",
    "WalkForwardExperiment",
]
