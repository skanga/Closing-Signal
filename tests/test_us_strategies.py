"""Positive-path and validation tests for every U.S. strategy family."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from closing_signal.strategy.framework import PointInTimeDataView, SignalBar
from closing_signal.strategy.us_strategies import (
    GapUpShakeoutParameters,
    GapUpShakeoutStrategy,
    HighTightFlagParameters,
    HighTightFlagStrategy,
    RelativeStrengthBreakoutStrategy,
    RelativeStrengthParameters,
    TurtleBreakoutParameters,
    TurtleBreakoutStrategy,
    UptrendShockReversalParameters,
    UptrendShockReversalStrategy,
)


def _bar(
    index: int,
    close: str,
    *,
    symbol: str = "TEST",
    open_price: str | None = None,
    high: str | None = None,
    low: str | None = None,
    volume: int = 100,
) -> SignalBar:
    session = date(2025, 1, 1) + timedelta(days=index)
    close_value = Decimal(close)
    open_value = Decimal(open_price or close)
    high_value = Decimal(high) if high else max(open_value, close_value) + Decimal("0.5")
    low_value = Decimal(low) if low else min(open_value, close_value) - Decimal("0.5")
    return SignalBar(
        symbol=symbol,
        session_date=session,
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume,
        dollar_volume=close_value * volume,
    )


def _view(series: dict[str, list[SignalBar]]) -> PointInTimeDataView:
    cutoff = max(bar.session_date for bars in series.values() for bar in bars)
    return PointInTimeDataView(
        series,
        cutoff_session=cutoff,
        cutoff_at=datetime.combine(cutoff, datetime.max.time(), UTC),
        universe_snapshot_id=f"universe:{cutoff}",
    )


def test_high_tight_flag_matches_momentum_tightness_and_contraction() -> None:
    bars = [_bar(index, str(10 + index / 2)) for index in range(20)]
    bars.extend(_bar(index, "20", volume=30 if index == 29 else 100) for index in range(20, 30))
    strategy = HighTightFlagStrategy(
        HighTightFlagParameters(
            momentum_window=30,
            momentum_ratio="1.5",
            consolidation_window=10,
            consolidation_ratio="1.1",
            minimum_high_retention="0.8",
            volume_window=5,
            maximum_volume_ratio="0.5",
        )
    )

    result = strategy.evaluate(_view({"TEST": bars}))

    assert result[0].symbol == "TEST"
    assert "volume_contraction" in result[0].matched_conditions


def test_turtle_breakout_uses_configured_dollar_volume_ranking() -> None:
    bars = [_bar(0, "9"), _bar(1, "10"), _bar(2, "10"), _bar(3, "12", open_price="11")]
    strategy = TurtleBreakoutStrategy(
        TurtleBreakoutParameters(
            breakout_window=3,
            minimum_dollar_volume="1000",
            ranking_basis="dollar_volume",
            require_bullish_body=True,
        )
    )

    result = strategy.evaluate(_view({"TEST": bars}))

    assert result[0].metrics["dollar_volume"] == Decimal("1200")


def test_relative_strength_is_cross_sectional_and_near_prior_high() -> None:
    strong = [_bar(index, str(10 + index * 2), symbol="STRONG") for index in range(4)]
    weak = [_bar(index, str(10 - index), symbol="WEAK") for index in range(4)]
    strategy = RelativeStrengthBreakoutStrategy(
        RelativeStrengthParameters(
            return_window=3,
            minimum_percentile="100",
            breakout_window=3,
            minimum_high_proximity="0.9",
        )
    )

    result = strategy.evaluate(_view({"STRONG": strong, "WEAK": weak}))

    assert [selection.symbol for selection in result] == ["STRONG"]
    assert result[0].metrics["percentile"] == Decimal("100")


def test_gap_up_shakeout_replaces_fixed_price_limit_logic() -> None:
    bars = [
        _bar(0, "10", high="10.5", low="9.5"),
        _bar(1, "11", open_price="11", high="11.5", low="10.8"),
        _bar(2, "11.5", open_price="12", high="12.2", low="10.9", volume=300),
    ]
    strategy = GapUpShakeoutStrategy(
        GapUpShakeoutParameters(
            minimum_gap="0.05",
            maximum_gap="0.2",
            volume_multiple="2",
            support_tolerance="0.02",
            require_bearish_shakeout=True,
        )
    )

    result = strategy.evaluate(_view({"TEST": bars}))

    assert result[0].matched_conditions == (
        "bounded_gap_up",
        "shakeout_volume",
        "support_hold",
    )


def test_uptrend_shock_reversal_uses_atr_volume_and_close_location() -> None:
    bars = [
        _bar(index, str(10 + index), high=str(11 + index), low=str(9 + index)) for index in range(4)
    ]
    bars.append(_bar(4, "12.8", open_price="11", high="13", low="10", volume=300))
    strategy = UptrendShockReversalStrategy(
        UptrendShockReversalParameters(
            fast_trend_window=2,
            slow_trend_window=4,
            atr_window=2,
            minimum_down_return="0.2",
            minimum_atr_multiple="1",
            volume_window=2,
            volume_multiple="2",
            minimum_close_location="0.8",
        )
    )

    result = strategy.evaluate(_view({"TEST": bars}))

    assert result[0].metrics["close_location"] > Decimal("0.9")


@pytest.mark.parametrize(
    "parameters",
    [
        {"fast_window": 20, "slow_window": 5, "volume_window": 5, "volume_multiple": 1},
        {
            "minimum_gap": "0.2",
            "maximum_gap": "0.1",
            "volume_multiple": 1,
            "support_tolerance": 0,
            "require_bearish_shakeout": True,
        },
    ],
)
def test_invalid_parameter_relationships_are_rejected(parameters: dict[str, object]) -> None:
    from closing_signal.strategy.us_strategies import MovingAverageVolumeParameters

    model = (
        MovingAverageVolumeParameters if "fast_window" in parameters else GapUpShakeoutParameters
    )
    with pytest.raises(ValidationError):
        model.model_validate(parameters)
