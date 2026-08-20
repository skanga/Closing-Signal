"""Offline integration tests for operator workflows and persisted summaries."""

import argparse
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import ClassVar

from closing_signal.data.repository import SQLiteRepository
from closing_signal.domain.models import DailyBar, Exchange, Instrument, InstrumentType
from closing_signal.market.calendar import MarketSession
from closing_signal.notify.delivery import DeliveryResult
from closing_signal.operations import (
    health_check,
    run_backtest,
    screen,
    sec_sync,
    sync_daily,
    sync_universe,
)
from closing_signal.providers.alpaca import InstrumentFetchResult, RejectedInstrument
from closing_signal.sec.edgar import SECFiling
from closing_signal.strategy.framework import PointInTimeDataView, StrategySelection


def _instrument(symbol: str = "TEST", instrument_id: str = "asset-test") -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        canonical_symbol=symbol,
        provider_symbol=symbol,
        name=f"{symbol} Incorporated",
        exchange=Exchange.NASDAQ,
        instrument_type=InstrumentType.COMMON_STOCK,
        status="active",
        tradable=True,
        first_observed=date(2026, 1, 1),
        last_observed=date(2026, 1, 3),
    )


def _daily_bar(symbol: str, instrument_id: str, day: int, adjustment: str) -> DailyBar:
    price = Decimal(9 + day)
    return DailyBar(
        instrument_id=instrument_id,
        session_date=date(2026, 1, day),
        source_timestamp=datetime(2026, 1, day, 21, tzinfo=UTC),
        provider="alpaca",
        feed="sip",
        frequency="1Day",
        open=price,
        high=price + 1,
        low=price - 1,
        close=price,
        volume=100,
        adjustment=adjustment,
    )


def _subscriber(email: str, categories: list[str]) -> dict[str, object]:
    return {
        "email": email,
        "active": True,
        "categories": categories,
        "consent_source": "double_opt_in_email",
        "policy_version": "privacy-v1",
        "consented_at": "2026-01-01T17:00:00+00:00",
        "confirmed_at": "2026-01-01T17:05:00+00:00",
        "deactivated_at": None,
        "deactivation_reason": None,
    }


class SelectingStrategy:
    strategy_id = "selecting"
    version = "1"
    parameters: ClassVar[dict[str, object]] = {"fixture": True}

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]:
        if "TEST" not in view.symbols:
            return []
        return [StrategySelection("TEST", 1, ("fixture",), {"score": Decimal(1)})]


class FakeDelivery:
    def __init__(self) -> None:
        self.messages = 0

    def deliver(self, message, *, recipients, dry_run=False) -> DeliveryResult:
        del message
        self.messages += 1
        if dry_run:
            return DeliveryResult(dry_run=tuple(recipients))
        return DeliveryResult(succeeded=tuple(recipients))


def test_sync_universe_persists_accepted_and_quarantines_rejected(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    client = SimpleNamespace(
        fetch_instruments=lambda observed_on: InstrumentFetchResult(
            (_instrument(),), (RejectedInstrument("OTCM", "venue is not NYSE or Nasdaq"),)
        )
    )
    monkeypatch.setattr(
        "closing_signal.operations.build_alpaca", lambda settings, progress: client
    )

    status = sync_universe(
        argparse.Namespace(as_of=date(2026, 1, 3)), SimpleNamespace(), repository
    )

    assert status == 0
    assert repository.count("instruments") == 1
    assert repository.count("quarantined_records") == 1


def test_sync_universe_failure_reports_bounded_actionable_reasons(
    tmp_path, monkeypatch, capsys
) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    rejected = tuple(
        [RejectedInstrument(f"OTC{index}", "venue is not NYSE or Nasdaq") for index in range(5)]
        + [RejectedInstrument("AAPL", "OpenFIGI API error: invalid request")]
    )
    client = SimpleNamespace(
        fetch_instruments=lambda observed_on: InstrumentFetchResult((), rejected)
    )
    monkeypatch.setattr(
        "closing_signal.operations.build_alpaca",
        lambda settings, progress: client,
    )
    events = []

    status = sync_universe(
        argparse.Namespace(as_of=date(2026, 8, 19)),
        SimpleNamespace(),
        repository,
        events.append,
    )
    summary = json.loads(capsys.readouterr().out)

    assert status == 4
    assert summary["rejection_reasons"] == [
        {
            "reason": "venue is not NYSE or Nasdaq",
            "count": 5,
            "examples": ["OTC0", "OTC1", "OTC2"],
        },
        {
            "reason": "OpenFIGI API error: invalid request",
            "count": 1,
            "examples": ["AAPL"],
        },
    ]
    assert "rerun sync-universe" in summary["next_step"]
    assert [event.message for event in events] == [
        "Fetching the Alpaca asset catalog",
        "Persisting instruments and quarantine findings",
    ]


def test_sync_daily_ingests_all_adjustments_and_actions(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    repository.upsert_instruments([_instrument()])

    class Provider:
        requested_ranges: ClassVar[list[tuple[date, date]]] = []

        def fetch_daily_bars(self, *, symbols, start, end, adjustment="raw"):
            self.requested_ranges.append((start, end))
            return [_daily_bar(symbol, symbol, 3, adjustment) for symbol in symbols]

        def fetch_corporate_actions(self, *, start, end, symbols):
            del start, end, symbols
            return []

        def fetch_calendar(self, *, start, end):
            del start
            return [
                SimpleNamespace(
                    session_date=date(2026, 1, day),
                    close_at=datetime(2026, 8, 18, 20, tzinfo=UTC),
                )
                for day in range(1, end.day + 1)
            ]

    provider = Provider()
    monkeypatch.setattr("closing_signal.operations.build_alpaca", lambda settings: provider)
    settings = SimpleNamespace(
        historical_refetch_sessions=3,
        corporate_action_refetch_sessions=3,
        alpaca_feed="sip",
        ingestion_chunk_size=100,
        finalization_delay_minutes=1,
    )

    status = sync_daily(argparse.Namespace(session=date(2026, 1, 3)), settings, repository)

    assert status == 0
    assert repository.count("daily_bars") == 3
    assert provider.requested_ranges == [
        (date(2026, 1, 1), date(2026, 1, 3)),
        (date(2026, 1, 1), date(2026, 1, 3)),
        (date(2026, 1, 1), date(2026, 1, 3)),
    ]


def test_screen_persists_structured_result_and_routes_email(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    instrument = _instrument()
    repository.upsert_instruments([instrument])
    repository.replace_universe_snapshot(date(2026, 1, 3), [instrument.instrument_id])
    repository.upsert_daily_bars(
        [_daily_bar("TEST", instrument.instrument_id, day, "split") for day in range(1, 4)]
    )
    subscriber_file = tmp_path / "subscribers.json"
    subscriber_file.write_text(
        json.dumps([_subscriber("reader@example.com", ["strategy:selecting"])]),
        encoding="utf-8",
    )
    delivery = FakeDelivery()
    monkeypatch.setattr("closing_signal.operations.build_alpaca", lambda settings: object())
    monkeypatch.setattr(
        "closing_signal.operations._latest_completed_session",
        lambda client, settings: SimpleNamespace(session_date=date(2026, 8, 18)),
    )
    monkeypatch.setattr(
        "closing_signal.operations.load_strategies",
        lambda path, expected_version: [SelectingStrategy()],
    )
    monkeypatch.setattr(
        "closing_signal.operations._delivery_service", lambda settings, repo: delivery
    )
    settings = SimpleNamespace(
        strategy_parameters_file=tmp_path / "strategies.json",
        strategy_config_version="v1",
        subscriber_file=subscriber_file,
        email_template_version="v1",
        security_link_template="https://example.test/{symbol}",
    )

    status = screen(
        argparse.Namespace(session=date(2026, 1, 3), dry_run=False, reprocess=False),
        settings,
        repository,
    )

    assert status == 0
    assert repository.count("strategy_runs") == 1
    assert repository.count("strategy_selections") == 1
    assert repository.count("subscription_events") == 1
    assert delivery.messages == 1


def test_sec_sync_delivers_candidate_and_persists_accession(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    repository.upsert_instruments([_instrument()])
    subscriber_file = tmp_path / "subscribers.json"
    subscriber_file.write_text(
        json.dumps([_subscriber("reader@example.com", ["sec"])]),
        encoding="utf-8",
    )
    rules = tmp_path / "rules.json"
    rules.write_text(
        json.dumps({"registered_direct": ["registered direct offering"]}), encoding="utf-8"
    )
    filing = SECFiling(
        accession_number="0000000001-26-000001",
        cik=1,
        issuer="Test Incorporated",
        symbol="TEST",
        form="S-3",
        filing_date=date(2026, 1, 3),
        accepted_at=datetime(2026, 1, 3, 17, tzinfo=UTC),
        primary_document="s3.htm",
        source_url="https://www.sec.gov/Archives/s3.htm",
    )

    class FakeEdgar:
        boundaries: ClassVar[list[date]] = []

        def __init__(self, **kwargs) -> None:
            del kwargs

        def fetch_company_tickers(self):
            return {"TEST": 1}

        def discover_filings(self, *, cik, symbol, filing_date_from):
            del cik, symbol
            self.boundaries.append(filing_date_from)
            return [filing]

        def fetch_document_text(self, candidate):
            del candidate
            return "registered direct offering"

    delivery = FakeDelivery()
    monkeypatch.setattr("closing_signal.operations.EdgarClient", FakeEdgar)
    monkeypatch.setattr(
        "closing_signal.operations._delivery_service", lambda settings, repo: delivery
    )
    settings = SimpleNamespace(
        sec_classification_rules_file=rules,
        sec_organization="Example LLC",
        sec_contact_email="ops@example.com",
        sec_candidate_forms=("S-3",),
        sec_history_start=date(2016, 1, 1),
        subscriber_file=subscriber_file,
        email_template_version="v1",
        http_max_attempts=1,
        http_base_delay_seconds=0,
        http_max_delay_seconds=0,
        http_jitter_seconds=0,
    )

    status = sec_sync(argparse.Namespace(dry_run=False), settings, repository)
    second_status = sec_sync(argparse.Namespace(dry_run=False), settings, repository)

    assert status == 0
    assert second_status == 0
    assert repository.count("sec_filings") == 1
    assert delivery.messages == 1
    assert FakeEdgar.boundaries == [date(2016, 1, 1), date(2026, 1, 3)]


def test_backtest_operation_writes_report_bundle(tmp_path, monkeypatch) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    test_instrument = _instrument()
    benchmark = _instrument("BENCH", "asset-bench")
    repository.upsert_instruments([test_instrument, benchmark])
    for day in range(1, 4):
        repository.replace_universe_snapshot(date(2026, 1, day), [test_instrument.instrument_id])
    repository.upsert_daily_bars(
        [
            _daily_bar(instrument.canonical_symbol, instrument.instrument_id, day, "split")
            for instrument in (test_instrument, benchmark)
            for day in range(1, 4)
        ]
    )
    monkeypatch.setattr(
        "closing_signal.operations.load_strategies",
        lambda path, expected_version: [SelectingStrategy()],
    )
    output = tmp_path / "report"
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "version": "bt-v1",
                "mode": "single",
                "strategy_id": "selecting",
                "output_directory": str(output),
                "configuration": {
                    "start": "2026-01-01",
                    "end": "2026-01-03",
                    "benchmark_symbol": "BENCH",
                    "execution": "next_session_open",
                    "initial_capital": "10000",
                    "position_size_fraction": "0.5",
                    "holding_sessions": 1,
                    "fixed_fee": "0",
                    "per_share_fee": "0",
                    "percentage_fee": "0",
                    "minimum_fee": "0",
                    "slippage_bps": "0",
                    "annual_risk_free_rate": "0",
                    "random_seed": 1,
                    "evaluation_segment": "out_of_sample",
                    "rebalance_rule": "daily",
                    "holding_rule": "fixed_sessions",
                    "position_sizing": "fraction_of_initial_capital",
                    "missing_exit_policy": "fail",
                    "strategy_config_version": "v1",
                    "universe_version": "fixture",
                    "data_version": "fixture",
                    "code_version": "fixture",
                },
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        backtest_config_version="bt-v1",
        strategy_parameters_file=tmp_path / "strategies.json",
        strategy_config_version="v1",
    )

    status = run_backtest(argparse.Namespace(request=request), settings, repository)

    assert status == 0
    assert (output / "manifest.json").exists()


def test_backtest_operation_runs_walk_forward_and_writes_isolated_folds(
    tmp_path, monkeypatch
) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    test_instrument = _instrument()
    benchmark = _instrument("BENCH", "asset-bench")
    repository.upsert_instruments([test_instrument, benchmark])
    for day in range(1, 8):
        repository.replace_universe_snapshot(date(2026, 1, day), [test_instrument.instrument_id])
    repository.upsert_daily_bars(
        [
            _daily_bar(instrument.canonical_symbol, instrument.instrument_id, day, adjustment)
            for instrument in (test_instrument, benchmark)
            for day in range(1, 8)
            for adjustment in ("split", "all")
        ]
    )
    monkeypatch.setattr(
        "closing_signal.operations.load_strategies",
        lambda path, expected_version: [SelectingStrategy()],
    )
    monkeypatch.setattr(
        "closing_signal.operations.build_strategy",
        lambda strategy_id, parameters: SelectingStrategy(),
    )
    output = tmp_path / "walk-report"
    request = tmp_path / "walk-request.json"
    request.write_text(
        json.dumps(
            {
                "version": "bt-v1",
                "mode": "walk_forward",
                "strategy_id": "selecting",
                "output_directory": str(output),
                "configuration": {
                    "start": "2026-01-01",
                    "end": "2026-01-07",
                    "benchmark_symbol": "BENCH",
                    "execution": "next_session_open",
                    "initial_capital": "10000",
                    "position_size_fraction": "0.5",
                    "holding_sessions": 1,
                    "fixed_fee": "0",
                    "per_share_fee": "0",
                    "percentage_fee": "0",
                    "minimum_fee": "0",
                    "slippage_bps": "0",
                    "annual_risk_free_rate": "0",
                    "random_seed": 1,
                    "evaluation_segment": "in_sample",
                    "rebalance_rule": "daily",
                    "holding_rule": "fixed_sessions",
                    "position_sizing": "fraction_of_initial_capital",
                    "missing_exit_policy": "fail",
                    "strategy_config_version": "v1",
                    "universe_version": "fixture",
                    "data_version": "fixture",
                    "code_version": "fixture",
                },
                "candidate_parameters": [{"candidate": 1}, {"candidate": 2}],
                "walk_forward": {
                    "train_sessions": 2,
                    "validation_sessions": 2,
                    "test_sessions": 2,
                    "step_sessions": 1,
                    "mode": "rolling",
                },
                "selection": {"metric": "total_return", "direction": "maximize"},
            }
        ),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        backtest_config_version="bt-v1",
        strategy_parameters_file=tmp_path / "strategies.json",
        strategy_config_version="v1",
    )

    status = run_backtest(argparse.Namespace(request=request), settings, repository)

    assert status == 0
    assert (output / "experiment-manifest.json").exists()
    assert (output / "fold-001" / "out-of-sample" / "metrics.json").exists()
    assert (output / "fold-002" / "candidate-002" / "validation" / "metrics.json").exists()


def test_health_check_uses_persisted_schedule_market_database_disk_and_smtp_evidence(
    tmp_path, monkeypatch, capsys
) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    instrument = _instrument()
    repository.upsert_instruments([instrument])
    repository.upsert_daily_bars([_daily_bar("TEST", instrument.instrument_id, 3, "split")])
    repository.start_operation_run("sec-ok", "sec-sync")
    repository.finish_operation_run("sec-ok", status="complete", exit_code=0, error_type=None)

    class FakeAlpaca:
        def fetch_calendar(self, *, start, end):
            del start, end
            return [
                MarketSession(
                    date(2026, 1, 3),
                    datetime(2026, 1, 3, 14, tzinfo=UTC),
                    datetime(2026, 1, 3, 21, tzinfo=UTC),
                )
            ]

    class FakeSMTP:
        def probe(self) -> None:
            return None

    monkeypatch.setattr("closing_signal.operations.build_alpaca", lambda settings: FakeAlpaca())
    monkeypatch.setattr("closing_signal.operations._smtp_transport", lambda settings: FakeSMTP())
    settings = SimpleNamespace(
        finalization_delay_minutes=1,
        health_market_max_age_sessions=0,
        health_required_operations=("sec-sync",),
        health_operation_max_age_hours=1,
        health_min_free_disk_bytes=1,
    )

    status = health_check(argparse.Namespace(), settings, repository)
    summary = json.loads(capsys.readouterr().out)

    assert status == 0
    assert summary["status"] == "healthy"
    assert all(check["status"] == "pass" for check in summary["checks"])


def test_health_check_is_unhealthy_when_required_schedule_has_never_run(
    tmp_path, monkeypatch, capsys
) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")

    class FakeAlpaca:
        def fetch_calendar(self, *, start, end):
            del start, end
            return []

    class FakeSMTP:
        def probe(self) -> None:
            return None

    monkeypatch.setattr("closing_signal.operations.build_alpaca", lambda settings: FakeAlpaca())
    monkeypatch.setattr("closing_signal.operations._smtp_transport", lambda settings: FakeSMTP())
    settings = SimpleNamespace(
        finalization_delay_minutes=1,
        health_market_max_age_sessions=0,
        health_required_operations=("sec-sync",),
        health_operation_max_age_hours=1,
        health_min_free_disk_bytes=1,
    )

    status = health_check(argparse.Namespace(), settings, repository)
    summary = json.loads(capsys.readouterr().out)

    assert status == 4
    assert summary["status"] == "unhealthy"
    assert any(check["name"] == "schedule:sec-sync" for check in summary["checks"])
