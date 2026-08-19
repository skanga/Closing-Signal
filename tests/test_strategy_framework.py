"""Point-in-time and structured strategy-engine contracts."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

from closing_signal.strategy.framework import (
    PointInTimeDataView,
    SignalBar,
    StrategyRunner,
    StrategyStatus,
)
from closing_signal.strategy.us_strategies import (
    MovingAverageVolumeParameters,
    MovingAverageVolumeStrategy,
)


def _bar(day: int, close: str, volume: int = 100) -> SignalBar:
    price = Decimal(close)
    return SignalBar(
        symbol="TEST",
        session_date=date(2026, 1, day),
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=volume,
        dollar_volume=price * volume,
    )


def test_point_in_time_view_never_exposes_future_bars() -> None:
    view = PointInTimeDataView(
        {"TEST": [_bar(1, "10"), _bar(2, "11"), _bar(3, "12")]},
        cutoff_session=date(2026, 1, 2),
        cutoff_at=datetime(2026, 1, 2, 22, tzinfo=UTC),
        universe_snapshot_id="universe:2026-01-02",
    )

    assert [bar.session_date for bar in view.bars("TEST")] == [date(2026, 1, 1), date(2026, 1, 2)]


def test_moving_average_strategy_returns_structured_deterministic_selection() -> None:
    bars = [
        _bar(1, "10"),
        _bar(2, "10"),
        _bar(3, "10"),
        _bar(4, "9"),
        _bar(5, "12", 500),
    ]
    view = PointInTimeDataView(
        {"TEST": bars},
        cutoff_session=date(2026, 1, 5),
        cutoff_at=datetime(2026, 1, 5, 22, tzinfo=UTC),
        universe_snapshot_id="universe:2026-01-05",
    )
    strategy = MovingAverageVolumeStrategy(
        MovingAverageVolumeParameters(
            fast_window=2,
            slow_window=3,
            volume_window=3,
            volume_multiple=Decimal("2"),
        )
    )

    first = strategy.evaluate(view)
    second = strategy.evaluate(view)

    assert first == second
    assert first[0].symbol == "TEST"
    assert set(first[0].matched_conditions) == {"bullish_ma_cross", "volume_confirmation"}
    assert "fast_ma" in first[0].metrics


class BrokenStrategy:
    strategy_id = "broken"
    version = "1"
    parameters: ClassVar[dict[str, object]] = {"config": "test"}

    def evaluate(self, view: PointInTimeDataView):
        del view
        raise RuntimeError("calculation failed")


def test_runner_records_failure_separately_from_no_selections() -> None:
    view = PointInTimeDataView(
        {},
        cutoff_session=date(2026, 1, 5),
        cutoff_at=datetime(2026, 1, 5, 22, tzinfo=UTC),
        universe_snapshot_id="universe:2026-01-05",
    )

    result = StrategyRunner().run(BrokenStrategy(), view)

    assert result.status is StrategyStatus.FAILED
    assert result.selections == ()
    assert result.error == "calculation failed"


def test_runner_never_reports_insufficient_history_as_zero_selections() -> None:
    view = PointInTimeDataView(
        {"SHORT": [_bar(1, "10")], "READY": [_bar(day, "10") for day in range(1, 5)]},
        cutoff_session=date(2026, 1, 5),
        cutoff_at=datetime(2026, 1, 5, 22, tzinfo=UTC),
        universe_snapshot_id="universe:2026-01-05",
    )
    strategy = MovingAverageVolumeStrategy(
        MovingAverageVolumeParameters(
            fast_window=2,
            slow_window=3,
            volume_window=3,
            volume_multiple=Decimal("2"),
        )
    )

    result = StrategyRunner().run(strategy, view)

    assert result.status is StrategyStatus.PARTIAL
    assert result.symbols_evaluated == 1
    assert result.symbols_skipped == 1

    insufficient_view = PointInTimeDataView(
        {"SHORT": [_bar(1, "10")]},
        cutoff_session=date(2026, 1, 1),
        cutoff_at=datetime(2026, 1, 1, 22, tzinfo=UTC),
        universe_snapshot_id="universe:2026-01-01",
    )
    failed = StrategyRunner().run(strategy, insufficient_view)

    assert failed.status is StrategyStatus.FAILED
    assert failed.symbols_evaluated == 0
    assert failed.symbols_skipped == 1
    assert "insufficient history" in (failed.error or "")
