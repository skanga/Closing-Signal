"""Strict operator request contracts for single and walk-forward backtests."""

import pytest
from pydantic import ValidationError

from closing_signal.backtest.request import WalkForwardBacktestRequest, parse_backtest_request


def _configuration() -> dict[str, object]:
    return {
        "start": "2016-01-01",
        "end": "2025-12-31",
        "benchmark_symbol": "SPY",
        "execution": "next_session_open",
        "initial_capital": "100000",
        "position_size_fraction": "0.1",
        "holding_sessions": 10,
        "fixed_fee": "0",
        "per_share_fee": "0.005",
        "percentage_fee": "0",
        "minimum_fee": "1",
        "slippage_bps": "5",
        "annual_risk_free_rate": "0.03",
        "random_seed": 7,
        "evaluation_segment": "in_sample",
        "rebalance_rule": "daily",
        "holding_rule": "fixed_sessions",
        "position_sizing": "fraction_of_initial_capital",
        "missing_exit_policy": "fail",
        "strategy_config_version": "v1",
        "universe_version": "daily-snapshots-v1",
        "data_version": "alpaca-v1",
        "code_version": "3.0.0",
    }


def test_walk_forward_request_requires_explicit_selection_and_candidates() -> None:
    request = parse_backtest_request(
        {
            "version": "bt-v1",
            "mode": "walk_forward",
            "strategy_id": "moving_average_volume",
            "output_directory": "reports/walk",
            "configuration": _configuration(),
            "candidate_parameters": [
                {
                    "fast_window": 5,
                    "slow_window": 20,
                    "volume_window": 20,
                    "volume_multiple": "1.5",
                },
                {
                    "fast_window": 10,
                    "slow_window": 50,
                    "volume_window": 20,
                    "volume_multiple": "2",
                },
            ],
            "walk_forward": {
                "train_sessions": 500,
                "validation_sessions": 126,
                "test_sessions": 126,
                "step_sessions": 126,
                "mode": "anchored",
            },
            "selection": {"metric": "sharpe_ratio", "direction": "maximize"},
        }
    )

    assert isinstance(request, WalkForwardBacktestRequest)
    assert request.walk_forward.mode == "anchored"
    assert len(request.candidate_parameters) == 2


def test_walk_forward_request_rejects_one_candidate_and_unknown_fields() -> None:
    raw = {
        "version": "bt-v1",
        "mode": "walk_forward",
        "strategy_id": "moving_average_volume",
        "output_directory": "reports/walk",
        "configuration": _configuration(),
        "candidate_parameters": [{}],
        "walk_forward": {
            "train_sessions": 500,
            "validation_sessions": 126,
            "test_sessions": 126,
            "step_sessions": 126,
            "mode": "rolling",
        },
        "selection": {"metric": "total_return", "direction": "maximize"},
        "unexpected": True,
    }

    with pytest.raises(ValidationError):
        parse_backtest_request(raw)
