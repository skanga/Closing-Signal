"""Configurable U.S. technical strategies with no hidden production defaults."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import replace
from decimal import Decimal
from statistics import fmean
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from closing_signal.strategy.framework import PointInTimeDataView, SignalBar, StrategySelection


class StrategyParameters(BaseModel):
    """Immutable base for versionable, serializable strategy parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class _SymbolStrategy[ParameterT: StrategyParameters](ABC):
    """Evaluate symbols independently so one malformed history is isolated."""

    strategy_id: ClassVar[str]
    version: ClassVar[str] = "1"
    parameter_model: ParameterT

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.parameter_model.model_dump(mode="json")

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]:
        matches: list[StrategySelection] = []
        for symbol in view.symbols:
            try:
                selection = self.evaluate_symbol(symbol, view.bars(symbol))
            except (ArithmeticError, IndexError, ValueError):
                continue
            if selection is not None:
                matches.append(selection)
        matches.sort(key=lambda item: (-_score(item), item.symbol))
        return [replace(item, rank=index) for index, item in enumerate(matches, start=1)]

    @abstractmethod
    def evaluate_symbol(
        self, symbol: str, bars: Sequence[SignalBar]
    ) -> StrategySelection | None: ...


def _score(selection: StrategySelection) -> Decimal:
    score = selection.metrics.get("score", Decimal(0))
    return score if isinstance(score, Decimal) else Decimal(str(score))


def _mean(values: Sequence[int | Decimal]) -> Decimal:
    if not values:
        raise ValueError("cannot average an empty sequence")
    return Decimal(str(fmean(values)))


class MovingAverageVolumeParameters(StrategyParameters):
    fast_window: PositiveInt
    slow_window: PositiveInt
    volume_window: PositiveInt
    volume_multiple: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def windows_are_ordered(self) -> "MovingAverageVolumeParameters":
        if self.fast_window >= self.slow_window:
            raise ValueError("fast_window must be less than slow_window")
        return self


class MovingAverageVolumeStrategy(_SymbolStrategy[MovingAverageVolumeParameters]):
    strategy_id = "moving_average_volume"

    def __init__(self, parameters: MovingAverageVolumeParameters) -> None:
        self.parameter_model = parameters

    @property
    def minimum_history_sessions(self) -> int:
        p = self.parameter_model
        return max(p.slow_window + 1, p.volume_window + 1)

    def evaluate_symbol(self, symbol: str, bars: Sequence[SignalBar]) -> StrategySelection | None:
        p = self.parameter_model
        required = max(p.slow_window + 1, p.volume_window + 1)
        if len(bars) < required:
            return None
        previous_fast = _mean([bar.close for bar in bars[-p.fast_window - 1 : -1]])
        previous_slow = _mean([bar.close for bar in bars[-p.slow_window - 1 : -1]])
        fast = _mean([bar.close for bar in bars[-p.fast_window :]])
        slow = _mean([bar.close for bar in bars[-p.slow_window :]])
        baseline_volume = _mean([bar.volume for bar in bars[-p.volume_window - 1 : -1]])
        volume_ratio = Decimal(bars[-1].volume) / baseline_volume
        if previous_fast <= previous_slow and fast > slow and volume_ratio >= p.volume_multiple:
            return StrategySelection(
                symbol,
                0,
                ("bullish_ma_cross", "volume_confirmation"),
                {
                    "fast_ma": fast,
                    "slow_ma": slow,
                    "volume_ratio": volume_ratio,
                    "score": (fast / slow - 1) + volume_ratio,
                },
            )
        return None


class HighTightFlagParameters(StrategyParameters):
    momentum_window: PositiveInt
    momentum_ratio: Decimal = Field(gt=1)
    consolidation_window: PositiveInt
    consolidation_ratio: Decimal = Field(gt=1)
    minimum_high_retention: Decimal = Field(gt=0, le=1)
    volume_window: PositiveInt
    maximum_volume_ratio: Decimal = Field(gt=0)


class HighTightFlagStrategy(_SymbolStrategy[HighTightFlagParameters]):
    strategy_id = "high_tight_flag"

    def __init__(self, parameters: HighTightFlagParameters) -> None:
        self.parameter_model = parameters

    @property
    def minimum_history_sessions(self) -> int:
        p = self.parameter_model
        return max(p.momentum_window, p.consolidation_window, p.volume_window + 1)

    def evaluate_symbol(self, symbol: str, bars: Sequence[SignalBar]) -> StrategySelection | None:
        p = self.parameter_model
        required = max(p.momentum_window, p.consolidation_window, p.volume_window + 1)
        if len(bars) < required:
            return None
        momentum = bars[-p.momentum_window :]
        consolidation = bars[-p.consolidation_window :]
        high = max(bar.high for bar in momentum)
        low = min(bar.low for bar in momentum)
        tight_high = max(bar.high for bar in consolidation)
        tight_low = min(bar.low for bar in consolidation)
        baseline_volume = _mean([bar.volume for bar in bars[-p.volume_window - 1 : -1]])
        volume_ratio = Decimal(bars[-1].volume) / baseline_volume
        conditions = (
            high / low >= p.momentum_ratio,
            tight_high / tight_low <= p.consolidation_ratio,
            tight_low >= high * p.minimum_high_retention,
            volume_ratio <= p.maximum_volume_ratio,
        )
        if all(conditions):
            return StrategySelection(
                symbol,
                0,
                ("prior_momentum", "tight_consolidation", "high_retention", "volume_contraction"),
                {
                    "momentum_ratio": high / low,
                    "consolidation_ratio": tight_high / tight_low,
                    "volume_ratio": volume_ratio,
                    "score": tight_low / high,
                },
            )
        return None


class TurtleBreakoutParameters(StrategyParameters):
    breakout_window: PositiveInt
    minimum_dollar_volume: Decimal = Field(ge=0)
    ranking_basis: Literal["dollar_volume", "breakout_strength"]
    require_bullish_body: bool


class TurtleBreakoutStrategy(_SymbolStrategy[TurtleBreakoutParameters]):
    strategy_id = "turtle_breakout"

    def __init__(self, parameters: TurtleBreakoutParameters) -> None:
        self.parameter_model = parameters

    @property
    def minimum_history_sessions(self) -> int:
        return self.parameter_model.breakout_window + 1

    def evaluate_symbol(self, symbol: str, bars: Sequence[SignalBar]) -> StrategySelection | None:
        p = self.parameter_model
        if len(bars) < p.breakout_window + 1:
            return None
        current = bars[-1]
        prior_high = max(bar.high for bar in bars[-p.breakout_window - 1 : -1])
        strength = current.close / prior_high - 1
        bullish = current.close > current.open and current.close > bars[-2].close
        if (
            current.close > prior_high
            and current.dollar_volume >= p.minimum_dollar_volume
            and (bullish or not p.require_bullish_body)
        ):
            score = current.dollar_volume if p.ranking_basis == "dollar_volume" else strength
            return StrategySelection(
                symbol,
                0,
                ("channel_breakout", "dollar_volume", "bullish_confirmation"),
                {
                    "prior_high": prior_high,
                    "breakout_strength": strength,
                    "dollar_volume": current.dollar_volume,
                    "score": score,
                },
            )
        return None


class RelativeStrengthParameters(StrategyParameters):
    return_window: PositiveInt
    minimum_percentile: Decimal = Field(ge=0, le=100)
    breakout_window: PositiveInt
    minimum_high_proximity: Decimal = Field(gt=0, le=1)


class RelativeStrengthBreakoutStrategy:
    strategy_id = "relative_strength_breakout"
    version = "1"

    def __init__(self, parameters: RelativeStrengthParameters) -> None:
        self.parameter_model = parameters

    @property
    def parameters(self) -> Mapping[str, object]:
        return self.parameter_model.model_dump(mode="json")

    @property
    def minimum_history_sessions(self) -> int:
        p = self.parameter_model
        return max(p.return_window + 1, p.breakout_window + 1)

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]:
        p = self.parameter_model
        candidates: list[tuple[str, Decimal, Decimal]] = []
        required = max(p.return_window + 1, p.breakout_window + 1)
        for symbol in view.symbols:
            bars = view.bars(symbol)
            if len(bars) < required or bars[-p.return_window - 1].close == 0:
                continue
            total_return = bars[-1].close / bars[-p.return_window - 1].close - 1
            prior_high = max(bar.high for bar in bars[-p.breakout_window - 1 : -1])
            proximity = bars[-1].close / prior_high if prior_high else Decimal(0)
            candidates.append((symbol, total_return, proximity))
        selections: list[StrategySelection] = []
        for symbol, total_return, proximity in candidates:
            percentile = Decimal(
                100 * sum(other_return <= total_return for _, other_return, _ in candidates)
            ) / Decimal(len(candidates))
            if percentile >= p.minimum_percentile and proximity >= p.minimum_high_proximity:
                selections.append(
                    StrategySelection(
                        symbol,
                        0,
                        ("relative_strength", "near_breakout"),
                        {
                            "total_return": total_return,
                            "percentile": percentile,
                            "high_proximity": proximity,
                            "score": percentile,
                        },
                    )
                )
        selections.sort(key=lambda item: (-_score(item), item.symbol))
        return [replace(item, rank=index) for index, item in enumerate(selections, start=1)]


class GapUpShakeoutParameters(StrategyParameters):
    minimum_gap: Decimal = Field(gt=0)
    maximum_gap: Decimal = Field(gt=0)
    volume_multiple: Decimal = Field(gt=0)
    support_tolerance: Decimal = Field(ge=0, lt=1)
    require_bearish_shakeout: bool

    @model_validator(mode="after")
    def gap_range_is_ordered(self) -> "GapUpShakeoutParameters":
        if self.maximum_gap < self.minimum_gap:
            raise ValueError("maximum_gap must be at least minimum_gap")
        return self


class GapUpShakeoutStrategy(_SymbolStrategy[GapUpShakeoutParameters]):
    strategy_id = "gap_up_shakeout"

    def __init__(self, parameters: GapUpShakeoutParameters) -> None:
        self.parameter_model = parameters

    @property
    def minimum_history_sessions(self) -> int:
        return 3

    def evaluate_symbol(self, symbol: str, bars: Sequence[SignalBar]) -> StrategySelection | None:
        if len(bars) < 3:
            return None
        p = self.parameter_model
        before_gap, gap_day, shakeout = bars[-3:]
        gap = gap_day.open / before_gap.close - 1
        volume_ratio = Decimal(shakeout.volume) / Decimal(max(gap_day.volume, 1))
        support = gap_day.close * (1 - p.support_tolerance)
        bearish = shakeout.close < shakeout.open
        if (
            p.minimum_gap <= gap <= p.maximum_gap
            and volume_ratio >= p.volume_multiple
            and shakeout.low >= support
            and (bearish or not p.require_bearish_shakeout)
        ):
            return StrategySelection(
                symbol,
                0,
                ("bounded_gap_up", "shakeout_volume", "support_hold"),
                {
                    "gap": gap,
                    "volume_ratio": volume_ratio,
                    "support": support,
                    "score": gap + volume_ratio,
                },
            )
        return None


class UptrendShockReversalParameters(StrategyParameters):
    fast_trend_window: PositiveInt
    slow_trend_window: PositiveInt
    atr_window: PositiveInt
    minimum_down_return: Decimal = Field(gt=0)
    minimum_atr_multiple: Decimal = Field(gt=0)
    volume_window: PositiveInt
    volume_multiple: Decimal = Field(gt=0)
    minimum_close_location: Decimal = Field(ge=0, le=1)

    @model_validator(mode="after")
    def trend_windows_are_ordered(self) -> "UptrendShockReversalParameters":
        if self.fast_trend_window >= self.slow_trend_window:
            raise ValueError("fast_trend_window must be less than slow_trend_window")
        return self


class UptrendShockReversalStrategy(_SymbolStrategy[UptrendShockReversalParameters]):
    strategy_id = "uptrend_shock_reversal"

    def __init__(self, parameters: UptrendShockReversalParameters) -> None:
        self.parameter_model = parameters

    @property
    def minimum_history_sessions(self) -> int:
        p = self.parameter_model
        return max(p.slow_trend_window + 1, p.atr_window + 1, p.volume_window + 1)

    def evaluate_symbol(self, symbol: str, bars: Sequence[SignalBar]) -> StrategySelection | None:
        p = self.parameter_model
        required = max(p.slow_trend_window + 1, p.atr_window + 1, p.volume_window + 1)
        if len(bars) < required:
            return None
        prior = bars[:-1]
        current = bars[-1]
        fast = _mean([bar.close for bar in prior[-p.fast_trend_window :]])
        slow = _mean([bar.close for bar in prior[-p.slow_trend_window :]])
        down_return = prior[-1].close / current.low - 1 if current.low else Decimal(0)
        true_ranges = [
            max(
                bar.high - bar.low,
                abs(bar.high - previous.close),
                abs(bar.low - previous.close),
            )
            for previous, bar in zip(
                prior[-p.atr_window - 1 : -1], prior[-p.atr_window :], strict=True
            )
        ]
        atr = _mean(true_ranges)
        shock_multiple = (prior[-1].close - current.low) / atr if atr else Decimal(0)
        baseline_volume = _mean([bar.volume for bar in prior[-p.volume_window :]])
        volume_ratio = Decimal(current.volume) / baseline_volume
        intraday_range = current.high - current.low
        close_location = (
            (current.close - current.low) / intraday_range if intraday_range else Decimal(0)
        )
        if (
            fast > slow
            and down_return >= p.minimum_down_return
            and shock_multiple >= p.minimum_atr_multiple
            and volume_ratio >= p.volume_multiple
            and close_location >= p.minimum_close_location
        ):
            return StrategySelection(
                symbol,
                0,
                (
                    "established_uptrend",
                    "downside_shock",
                    "intraday_reversal",
                    "volume_confirmation",
                ),
                {
                    "fast_ma": fast,
                    "slow_ma": slow,
                    "down_return": down_return,
                    "shock_atr_multiple": shock_multiple,
                    "volume_ratio": volume_ratio,
                    "close_location": close_location,
                    "score": shock_multiple + close_location,
                },
            )
        return None
