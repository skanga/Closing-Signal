"""Provider-independent domain models for the U.S. equities product."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Exchange(StrEnum):
    """Listing venues admitted by the MVP universe."""

    NYSE = "NYSE"
    NASDAQ = "NASDAQ"


class InstrumentType(StrEnum):
    """Security types admitted by the MVP universe."""

    COMMON_STOCK = "common_stock"
    ETF = "etf"
    ADR = "adr"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Canonical identity and current provider metadata for one security."""

    instrument_id: str
    canonical_symbol: str
    provider_symbol: str
    name: str
    exchange: Exchange
    instrument_type: InstrumentType
    status: str
    tradable: bool
    first_observed: date
    last_observed: date

    def __post_init__(self) -> None:
        required = {
            "instrument_id": self.instrument_id,
            "canonical_symbol": self.canonical_symbol,
            "provider_symbol": self.provider_symbol,
            "name": self.name,
            "status": self.status,
        }
        missing = [key for key, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"instrument fields cannot be blank: {', '.join(missing)}")
        if self.last_observed < self.first_observed:
            raise ValueError("last_observed cannot precede first_observed")


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One raw, completed regular-session daily OHLCV bar.

    Dollar volume is defined as unadjusted close multiplied by raw volume. Adjusted
    series are intentionally separate from this source record.
    """

    instrument_id: str
    session_date: date
    source_timestamp: datetime
    provider: str
    feed: str
    frequency: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adjustment: str = "raw"

    def __post_init__(self) -> None:
        if self.source_timestamp.tzinfo is None:
            raise ValueError("source_timestamp must be timezone-aware")
        if not all((self.instrument_id, self.provider, self.feed, self.frequency, self.adjustment)):
            raise ValueError("bar identity fields cannot be blank")
        prices = (self.open, self.high, self.low, self.close)
        if any(price < 0 or not price.is_finite() for price in prices):
            raise ValueError("OHLC prices must be finite and non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high must be at least every other OHLC price")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low must be at most every other OHLC price")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")

    @property
    def dollar_volume(self) -> Decimal:
        """Return raw close times raw volume (DAT-009)."""
        return self.close * self.volume


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """Canonical provider action retaining all source fields for later correction."""

    provider_action_id: str
    provider: str
    action_type: str
    provider_symbol: str
    effective_date: date | None
    process_date: date | None
    ratio: Decimal | None
    cash_amount: Decimal | None
    new_symbol: str | None
    source_payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not all(
            (self.provider_action_id, self.provider, self.action_type, self.provider_symbol)
        ):
            raise ValueError("corporate action identity fields cannot be blank")
        if self.ratio is not None and (not self.ratio.is_finite() or self.ratio <= 0):
            raise ValueError("corporate action ratio must be finite and positive")
        if self.cash_amount is not None and (
            not self.cash_amount.is_finite() or self.cash_amount < 0
        ):
            raise ValueError("corporate action cash amount must be finite and non-negative")
