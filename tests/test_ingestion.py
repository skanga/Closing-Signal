"""Resumable chunk ingestion and run-diagnostic contracts."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from closing_signal.data.ingestion import MarketDataIngestionService
from closing_signal.data.repository import SQLiteRepository
from closing_signal.domain.models import DailyBar


def _bar(symbol: str, day: int) -> DailyBar:
    return DailyBar(
        instrument_id=symbol,
        session_date=date(2026, 1, day),
        source_timestamp=datetime(2026, 1, day, 5, tzinfo=UTC),
        provider="alpaca",
        feed="sip",
        frequency="1Day",
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=100,
    )


class FailOnceProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.failed = False

    def fetch_daily_bars(
        self, *, symbols: list[str], start: date, end: date, adjustment: str = "raw"
    ) -> Iterable[DailyBar]:
        del start, end, adjustment
        key = tuple(symbols)
        self.calls.append(key)
        if key == ("MSFT",) and not self.failed:
            self.failed = True
            raise ConnectionError("temporary provider failure")
        return [replace(_bar(symbol, 2), instrument_id=symbol) for symbol in symbols]


def test_partial_run_resumes_only_failed_chunks(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    provider = FailOnceProvider()
    service = MarketDataIngestionService(
        provider=provider,
        repository=repository,
        provider_name="alpaca",
        feed="sip",
        chunk_size=1,
    )
    identities = {"AAPL": "asset-aapl", "MSFT": "asset-msft"}

    first = service.sync(
        symbol_identities=identities,
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        expected_sessions=(date(2026, 1, 2),),
    )
    second = service.sync(
        symbol_identities=identities,
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        expected_sessions=(date(2026, 1, 2),),
    )

    assert first.status == "partial"
    assert second.status == "complete"
    assert provider.calls == [("AAPL",), ("MSFT",), ("MSFT",)]
    assert repository.count("daily_bars") == 2
    assert repository.count("ingestion_runs") == 1
    assert repository.count("ingestion_pages") == 2


def test_sync_reports_each_provider_chunk(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    provider = FailOnceProvider()
    provider.failed = True
    events = []
    service = MarketDataIngestionService(
        provider=provider,
        repository=repository,
        provider_name="alpaca",
        feed="sip",
        chunk_size=2,
        progress=events.append,
    )

    service.sync(
        symbol_identities={
            "AAPL": "asset-aapl",
            "GOOG": "asset-goog",
            "MSFT": "asset-msft",
        },
        start=date(2026, 1, 2),
        end=date(2026, 1, 2),
        expected_sessions=(date(2026, 1, 2),),
    )

    assert [(event.message, event.completed, event.total, event.unit) for event in events] == [
        ("Fetching raw daily bars", 1, 2, "chunks"),
        ("Fetching raw daily bars", 2, 2, "chunks"),
    ]


def test_missing_expected_session_is_quarantined(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    provider = FailOnceProvider()
    provider.failed = True
    service = MarketDataIngestionService(
        provider=provider,
        repository=repository,
        provider_name="alpaca",
        feed="sip",
        chunk_size=1,
    )

    service.sync(
        symbol_identities={"AAPL": "asset-aapl"},
        start=date(2026, 1, 2),
        end=date(2026, 1, 3),
        expected_sessions=(date(2026, 1, 2), date(2026, 1, 3)),
    )

    assert repository.count("quarantined_records") == 1
