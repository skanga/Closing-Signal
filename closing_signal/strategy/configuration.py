"""Load enabled strategies from explicit, versioned JSON configuration."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from closing_signal.strategy.framework import Strategy
from closing_signal.strategy.us_strategies import (
    GapUpShakeoutParameters,
    GapUpShakeoutStrategy,
    HighTightFlagParameters,
    HighTightFlagStrategy,
    MovingAverageVolumeParameters,
    MovingAverageVolumeStrategy,
    RelativeStrengthBreakoutStrategy,
    RelativeStrengthParameters,
    TurtleBreakoutParameters,
    TurtleBreakoutStrategy,
    UptrendShockReversalParameters,
    UptrendShockReversalStrategy,
)

type StrategyFactory = Callable[[dict[str, object]], Strategy]


def _moving_average(values: dict[str, object]) -> Strategy:
    return MovingAverageVolumeStrategy(MovingAverageVolumeParameters.model_validate(values))


def _high_tight_flag(values: dict[str, object]) -> Strategy:
    return HighTightFlagStrategy(HighTightFlagParameters.model_validate(values))


def _turtle(values: dict[str, object]) -> Strategy:
    return TurtleBreakoutStrategy(TurtleBreakoutParameters.model_validate(values))


def _relative_strength(values: dict[str, object]) -> Strategy:
    return RelativeStrengthBreakoutStrategy(RelativeStrengthParameters.model_validate(values))


def _gap_up(values: dict[str, object]) -> Strategy:
    return GapUpShakeoutStrategy(GapUpShakeoutParameters.model_validate(values))


def _shock_reversal(values: dict[str, object]) -> Strategy:
    return UptrendShockReversalStrategy(UptrendShockReversalParameters.model_validate(values))


_FACTORIES: dict[str, StrategyFactory] = {
    "moving_average_volume": _moving_average,
    "high_tight_flag": _high_tight_flag,
    "turtle_breakout": _turtle,
    "relative_strength_breakout": _relative_strength,
    "gap_up_shakeout": _gap_up,
    "uptrend_shock_reversal": _shock_reversal,
}


def build_strategy(strategy_id: str, parameters: dict[str, object]) -> Strategy:
    """Build one explicitly parameterized production strategy candidate."""
    try:
        factory = _FACTORIES[strategy_id]
    except KeyError as exc:
        raise ValueError(f"unknown strategy: {strategy_id}") from exc
    return factory(parameters)


def load_strategies(path: str | Path, *, expected_version: str) -> list[Strategy]:
    """Validate a strategy set and reject unknown or mismatched configuration."""
    parsed = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("strategy configuration must contain an object")
    root = cast(dict[str, Any], parsed)
    if root.get("version") != expected_version:
        raise ValueError("strategy configuration version does not match runtime configuration")
    raw_strategies = root.get("strategies")
    if not isinstance(raw_strategies, dict):
        raise ValueError("strategies must be an object")
    unknown = sorted(set(raw_strategies) - set(_FACTORIES))
    if unknown:
        raise ValueError(f"unknown strategy: {', '.join(unknown)}")
    strategies: list[Strategy] = []
    for strategy_id in sorted(raw_strategies):
        raw = raw_strategies[strategy_id]
        if not isinstance(raw, dict):
            raise ValueError(f"strategy {strategy_id} configuration must be an object")
        if raw.get("enabled") is not True:
            continue
        parameters = raw.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError(f"strategy {strategy_id} parameters must be an object")
        strategies.append(build_strategy(strategy_id, cast(dict[str, object], parameters)))
    return strategies
