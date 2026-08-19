"""Versioned strategy configuration loader contracts."""

import json

import pytest

from closing_signal.strategy.configuration import build_strategy, load_strategies


def test_loader_requires_version_match_and_builds_requested_strategy(tmp_path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(
        json.dumps(
            {
                "version": "research-7",
                "strategies": {
                    "moving_average_volume": {
                        "enabled": True,
                        "parameters": {
                            "fast_window": 5,
                            "slow_window": 20,
                            "volume_window": 20,
                            "volume_multiple": "1.5",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    strategies = load_strategies(path, expected_version="research-7")

    assert [strategy.strategy_id for strategy in strategies] == ["moving_average_volume"]
    assert strategies[0].parameters["fast_window"] == 5


def test_loader_rejects_unknown_strategy_instead_of_ignoring_it(tmp_path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(
        json.dumps(
            {
                "version": "v1",
                "strategies": {"mystery": {"enabled": True, "parameters": {}}},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown strategy"):
        load_strategies(path, expected_version="v1")


def test_loader_rejects_version_mismatch(tmp_path) -> None:
    path = tmp_path / "strategies.json"
    path.write_text(json.dumps({"version": "v1", "strategies": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="version"):
        load_strategies(path, expected_version="v2")


def test_build_strategy_validates_explicit_candidate_parameters() -> None:
    strategy = build_strategy(
        "moving_average_volume",
        {
            "fast_window": 5,
            "slow_window": 20,
            "volume_window": 20,
            "volume_multiple": "1.5",
        },
    )

    assert strategy.strategy_id == "moving_average_volume"
    assert strategy.parameters["slow_window"] == 20


def test_build_strategy_rejects_unknown_candidate() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("unknown", {})
