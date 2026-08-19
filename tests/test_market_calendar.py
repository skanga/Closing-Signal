"""Exchange-session completion tests for NYSE/Nasdaq EOD operation."""

from datetime import UTC, date, datetime, timedelta

from closing_signal.market.calendar import ExchangeCalendar, MarketSession


def _session(day: date, close_hour_utc: int) -> MarketSession:
    return MarketSession(
        session_date=day,
        open_at=datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC),
        close_at=datetime(day.year, day.month, day.day, close_hour_utc, tzinfo=UTC),
    )


def test_latest_completed_session_honors_finalization_delay() -> None:
    calendar = ExchangeCalendar(finalization_delay=timedelta(minutes=45))
    regular = _session(date(2026, 8, 17), 20)
    current = _session(date(2026, 8, 18), 20)

    assert (
        calendar.latest_completed_session(
            [regular, current], now=datetime(2026, 8, 18, 20, 30, tzinfo=UTC)
        )
        == regular
    )
    assert (
        calendar.latest_completed_session(
            [regular, current], now=datetime(2026, 8, 18, 20, 45, tzinfo=UTC)
        )
        == current
    )


def test_early_close_uses_the_session_specific_close_time() -> None:
    calendar = ExchangeCalendar(finalization_delay=timedelta(minutes=30))
    early_close = _session(date(2026, 11, 27), 18)

    assert (
        calendar.latest_completed_session(
            [early_close], now=datetime(2026, 11, 27, 18, 30, tzinfo=UTC)
        )
        == early_close
    )


def test_no_completed_session_returns_none() -> None:
    calendar = ExchangeCalendar(finalization_delay=timedelta(hours=1))
    current = _session(date(2026, 8, 18), 20)

    assert (
        calendar.latest_completed_session([current], now=datetime(2026, 8, 18, 20, 30, tzinfo=UTC))
        is None
    )
