"""Persistence tests for idempotency and point-in-time universe snapshots."""

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

from closing_signal.data.repository import SQLiteRepository
from closing_signal.domain.models import (
    CorporateAction,
    DailyBar,
    Exchange,
    Instrument,
    InstrumentType,
)


def _instrument() -> Instrument:
    return Instrument(
        instrument_id="asset-1",
        canonical_symbol="AAPL",
        provider_symbol="AAPL",
        name="Apple Inc.",
        exchange=Exchange.NASDAQ,
        instrument_type=InstrumentType.COMMON_STOCK,
        status="active",
        tradable=True,
        first_observed=date(2026, 8, 17),
        last_observed=date(2026, 8, 18),
    )


def _bar(close: str = "103") -> DailyBar:
    return DailyBar(
        instrument_id="asset-1",
        session_date=date(2026, 8, 17),
        source_timestamp=datetime(2026, 8, 17, 4, tzinfo=UTC),
        provider="alpaca",
        feed="sip",
        frequency="1Day",
        open=Decimal("100"),
        high=Decimal("104"),
        low=Decimal("99"),
        close=Decimal(close),
        volume=1200,
    )


def test_upserts_are_idempotent_and_preserve_instrument_identity(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    repository.upsert_instruments([_instrument()])
    repository.upsert_instruments([_instrument()])
    repository.upsert_daily_bars([_bar()])
    repository.upsert_daily_bars([_bar("102")])

    assert repository.count("instruments") == 1
    assert repository.count("daily_bars") == 1
    assert repository.get_daily_bars("asset-1")[0].close == Decimal("102")


def test_universe_snapshot_is_session_scoped_and_idempotent(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    repository.upsert_instruments([_instrument()])

    repository.replace_universe_snapshot(date(2026, 8, 17), ["asset-1"])
    repository.replace_universe_snapshot(date(2026, 8, 17), ["asset-1"])

    assert repository.get_universe_snapshot(date(2026, 8, 17)) == ["asset-1"]
    assert repository.count("universe_snapshot_members") == 1


def test_corporate_actions_and_quarantine_are_idempotent(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    action = CorporateAction(
        provider_action_id="ca-1",
        provider="alpaca",
        action_type="forward_split",
        provider_symbol="EXM",
        effective_date=date(2026, 8, 17),
        process_date=date(2026, 8, 18),
        ratio=Decimal("2"),
        cash_amount=None,
        new_symbol=None,
        source_payload={"id": "ca-1", "rate": "2"},
    )

    repository.upsert_corporate_actions([action, action])
    first_id = repository.quarantine(
        source="alpaca",
        record_type="daily_bar",
        reason="invalid OHLC relationship",
        payload={"symbol": "BAD", "h": 1, "l": 2},
    )
    second_id = repository.quarantine(
        source="alpaca",
        record_type="daily_bar",
        reason="invalid OHLC relationship",
        payload={"symbol": "BAD", "h": 1, "l": 2},
    )

    assert repository.count("corporate_actions") == 1
    assert repository.count("quarantined_records") == 1
    assert first_id == second_id


def test_symbol_change_preserves_historical_alias(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    original = _instrument()
    renamed = replace(
        original,
        canonical_symbol="APPLX",
        provider_symbol="APPLX",
        last_observed=date(2026, 8, 19),
    )

    repository.upsert_instruments([original])
    repository.upsert_instruments([renamed])

    assert repository.count("instruments") == 1
    assert repository.count("instrument_symbols") == 2


def test_pre_adjustment_schema_migrates_existing_rows_as_raw(tmp_path) -> None:
    path = tmp_path / "market.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("""
            CREATE TABLE daily_bars (
                instrument_id TEXT, session_date TEXT, source_timestamp TEXT,
                provider TEXT, feed TEXT, frequency TEXT, open TEXT, high TEXT,
                low TEXT, close TEXT, volume INTEGER, dollar_volume TEXT,
                PRIMARY KEY (instrument_id, session_date, provider, feed, frequency)
            )
            """)
        connection.execute(
            "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "asset-1",
                "2026-08-17",
                "2026-08-17T04:00:00+00:00",
                "alpaca",
                "sip",
                "1Day",
                "10",
                "11",
                "9",
                "10",
                100,
                "1000",
            ),
        )

    repository = SQLiteRepository(path)

    assert repository.get_daily_bars("asset-1", adjustment="raw")[0].adjustment == "raw"


def test_data_audit_quarantines_missing_series_and_inconsistent_factors(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    raw = _bar()
    split = replace(
        raw,
        open=Decimal("50"),
        high=Decimal("52"),
        low=Decimal("49"),
        close=Decimal("51"),
        adjustment="split",
    )
    repository.upsert_daily_bars([raw, split])

    audit_events = []
    findings = repository.run_data_audit(audit_events.append)

    assert findings == 2
    assert repository.count("quarantined_records") == 2
    assert [event.message for event in audit_events] == [
        "Scanning for incomplete adjustment series",
        "Checking split-factor consistency",
        "Persisting data-quality findings",
    ]


def test_operation_runs_preserve_started_failed_and_completed_state(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")

    repository.start_operation_run("run-1", "sec-sync")
    repository.finish_operation_run(
        "run-1", status="failed", exit_code=4, error_type="TimeoutError"
    )
    repository.start_operation_run("run-2", "sec-sync")
    repository.finish_operation_run("run-2", status="complete", exit_code=0, error_type=None)

    latest = repository.latest_operation_run("sec-sync")
    assert latest is not None
    assert latest["run_id"] == "run-2"
    assert latest["status"] == "complete"
    assert latest["finished_at"] is not None


def test_operation_lock_rejects_overlap_and_only_owner_can_release(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")

    assert repository.acquire_operation_lock("mutating-operation", "owner-1") is True
    assert repository.acquire_operation_lock("mutating-operation", "owner-2") is False
    repository.release_operation_lock("mutating-operation", "owner-2")
    assert repository.acquire_operation_lock("mutating-operation", "owner-2") is False
    repository.release_operation_lock("mutating-operation", "owner-1")
    assert repository.acquire_operation_lock("mutating-operation", "owner-2") is True
