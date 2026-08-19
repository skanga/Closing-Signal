"""Contract tests for the canonical U.S. equities domain."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from closing_signal.domain.models import DailyBar, Exchange, Instrument, InstrumentType


def test_instrument_preserves_us_symbol_punctuation() -> None:
    instrument = Instrument(
        instrument_id="alpaca:brk.b",
        canonical_symbol="BRK.B",
        provider_symbol="BRK.B",
        name="Berkshire Hathaway Class B",
        exchange=Exchange.NYSE,
        instrument_type=InstrumentType.COMMON_STOCK,
        status="active",
        tradable=True,
        first_observed=date(2026, 1, 2),
        last_observed=date(2026, 1, 2),
    )

    assert instrument.canonical_symbol == "BRK.B"


def test_daily_bar_calculates_documented_dollar_volume() -> None:
    bar = DailyBar(
        instrument_id="asset-1",
        session_date=date(2026, 1, 2),
        source_timestamp=datetime(2026, 1, 2, 21, tzinfo=UTC),
        provider="alpaca",
        feed="sip",
        frequency="1Day",
        open=Decimal("10"),
        high=Decimal("12"),
        low=Decimal("9"),
        close=Decimal("11"),
        volume=100,
    )

    assert bar.dollar_volume == Decimal("1100")


@pytest.mark.parametrize(
    ("open_price", "high", "low", "close", "volume"),
    [
        (Decimal("10"), Decimal("9"), Decimal("8"), Decimal("8.5"), 100),
        (Decimal("10"), Decimal("11"), Decimal("10.5"), Decimal("10.2"), 100),
        (Decimal("-1"), Decimal("1"), Decimal("0"), Decimal("1"), 100),
        (Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), -1),
    ],
)
def test_daily_bar_rejects_invalid_ohlcv(
    open_price: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: int
) -> None:
    with pytest.raises(ValueError):
        DailyBar(
            instrument_id="asset-1",
            session_date=date(2026, 1, 2),
            source_timestamp=datetime(2026, 1, 2, 21, tzinfo=UTC),
            provider="alpaca",
            feed="sip",
            frequency="1Day",
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
