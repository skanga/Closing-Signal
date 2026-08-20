"""Composable operator workflows used by the CLI and schedulers."""

import argparse
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from closing_signal.backtest.engine import BacktestConfig, BacktestEngine, BacktestProgress
from closing_signal.backtest.experiment import WalkForwardExperiment
from closing_signal.backtest.reporting import (
    write_backtest_artifacts,
    write_walk_forward_artifacts,
)
from closing_signal.backtest.request import (
    SingleBacktestRequest,
    WalkForwardBacktestRequest,
    parse_backtest_request,
)
from closing_signal.core.http import RetryPolicy
from closing_signal.core.progress import ProgressEvent, ProgressReporter, no_progress, should_report
from closing_signal.core.us_config import AppSettings
from closing_signal.data.ingestion import MarketDataIngestionService
from closing_signal.data.repository import SQLiteRepository
from closing_signal.market.calendar import ExchangeCalendar, MarketSession
from closing_signal.notify.delivery import EmailDeliveryService, SMTPTransport
from closing_signal.notify.email import EmailRenderer, NotificationContent
from closing_signal.notify.subscribers import SubscriberRegistry
from closing_signal.providers.alpaca import (
    AlpacaClient,
    AssetClassifier,
    JsonAssetClassifier,
    RejectedInstrument,
)
from closing_signal.providers.reference import (
    NasdaqDirectoryClient,
    OpenFigiClient,
    ReconciledAssetClassifier,
    SECCompanyTickerClient,
)
from closing_signal.sec.edgar import EdgarClient, FilingClassifier
from closing_signal.strategy.configuration import build_strategy, load_strategies
from closing_signal.strategy.framework import PointInTimeDataView, SignalBar, StrategyRunner


def validate_operational_files(settings: AppSettings) -> None:
    """Validate every referenced managed file before external work begins."""
    if settings.asset_classification_source == "json":
        assert settings.asset_classification_file is not None
        classifier = JsonAssetClassifier.load(settings.asset_classification_file)
        if not classifier.classifications:
            raise ValueError("asset classification file is empty")
    strategies = load_strategies(
        settings.strategy_parameters_file,
        expected_version=settings.strategy_config_version,
    )
    if not strategies:
        raise ValueError("no strategies are enabled")
    FilingClassifier(rules=_load_string_lists(settings.sec_classification_rules_file))
    registry = SubscriberRegistry.load(settings.subscriber_file)
    if not any(subscriber.active for subscriber in registry.subscribers):
        raise ValueError("subscriber file has no active recipients")


def build_alpaca(settings: AppSettings, progress: ProgressReporter = no_progress) -> AlpacaClient:
    """Construct the official provider adapter without exposing credentials."""
    return AlpacaClient(
        api_key=settings.alpaca_api_key.get_secret_value(),
        api_secret=settings.alpaca_api_secret.get_secret_value(),
        feed=settings.alpaca_feed,
        classifier=_asset_classifier(settings, progress),
        retry_policy=_retry_policy(settings),
        asset_base_url=settings.alpaca_asset_base_url,
    )


def _asset_classifier(
    settings: AppSettings, progress: ProgressReporter = no_progress
) -> AssetClassifier:
    if settings.asset_classification_source == "json":
        assert settings.asset_classification_file is not None
        return JsonAssetClassifier.load(settings.asset_classification_file)
    assert settings.openfigi_api_key is not None
    retry_policy = _retry_policy(settings)
    return ReconciledAssetClassifier(
        OpenFigiClient(
            api_key=settings.openfigi_api_key.get_secret_value(),
            retry_policy=retry_policy,
            progress=progress,
        ),
        NasdaqDirectoryClient(retry_policy=retry_policy),
        SECCompanyTickerClient(
            organization=settings.sec_organization,
            contact_email=settings.sec_contact_email,
            retry_policy=retry_policy,
        ),
        progress=progress,
    )


def _rejection_reasons(
    rejected: Sequence[RejectedInstrument],
) -> list[dict[str, object]]:
    counts = Counter(item.reason for item in rejected)
    examples: defaultdict[str, list[str]] = defaultdict(list)
    for item in rejected:
        selected = examples[item.reason]
        if item.provider_symbol not in selected and len(selected) < 3:
            selected.append(item.provider_symbol)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return [
        {"reason": reason, "count": count, "examples": examples[reason]}
        for reason, count in ordered
    ]


def sync_universe(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Synchronize mapped NYSE/Nasdaq securities and snapshot membership."""
    client = build_alpaca(settings, progress)
    observed_on = args.as_of or _latest_completed_session(client, settings).session_date
    progress(ProgressEvent("Fetching the Alpaca asset catalog"))
    result = client.fetch_instruments(observed_on=observed_on)
    progress(ProgressEvent("Persisting instruments and quarantine findings"))
    repository.upsert_instruments(result.accepted)
    repository.replace_universe_snapshot(
        observed_on,
        [instrument.instrument_id for instrument in result.accepted],
    )
    for rejected in result.rejected:
        repository.quarantine(
            source="alpaca",
            record_type="instrument",
            reason=rejected.reason,
            payload={"provider_symbol": rejected.provider_symbol},
        )
    for warning in result.warnings:
        repository.quarantine(
            source="reference-reconciliation",
            record_type="instrument_warning",
            reason=warning.reason,
            payload={"provider_symbol": warning.provider_symbol},
        )
    summary = {
        "status": "complete" if result.accepted else "failed",
        "session": observed_on.isoformat(),
        "accepted": len(result.accepted),
        "rejected": len(result.rejected),
        "warnings": len(result.warnings),
    }
    if not result.accepted:
        summary["rejection_reasons"] = _rejection_reasons(result.rejected)
        summary["next_step"] = (
            "Review rejection_reasons and provider credentials, then rerun sync-universe."
        )
    print(json.dumps(summary, sort_keys=True))
    return 0 if result.accepted else 4


def sync_daily(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Synchronize raw, split-adjusted, and all-adjusted completed daily bars."""
    client = build_alpaca(settings)
    latest = _latest_completed_session(client, settings).session_date
    session_date = args.session or latest
    if session_date > latest:
        raise ValueError("requested session is not completed")
    instruments = repository.list_instruments()
    if instruments:
        repository.replace_universe_snapshot(
            session_date, [instrument.instrument_id for instrument in instruments]
        )
    start = _session_lookback_start(client, session_date, settings.historical_refetch_sessions)
    action_start = _session_lookback_start(
        client, session_date, settings.corporate_action_refetch_sessions
    )
    return _sync_range(
        client,
        settings,
        repository,
        start,
        session_date,
        action_start=action_start,
        progress=progress,
    )


def backfill(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Backfill the confirmed 2016-forward history through the latest completion."""
    client = build_alpaca(settings)
    start = args.start or date(2016, 1, 1)
    latest = _latest_completed_session(client, settings).session_date
    end = args.end or latest
    if end > latest:
        raise ValueError("backfill end is not a completed session")
    return _sync_range(
        client, settings, repository, start, end, action_start=start, progress=progress
    )


def screen(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Run configured strategies and optionally deliver idempotent result email."""
    client = build_alpaca(settings)
    latest = _latest_completed_session(client, settings).session_date
    session_date = args.session or latest
    if session_date > latest:
        raise ValueError("screening session is not completed")
    snapshot_ids = repository.get_universe_snapshot(session_date)
    if not snapshot_ids:
        print('{"status":"failed","reason":"universe snapshot is missing"}')
        return 4
    progress(ProgressEvent("Preparing point-in-time screening data"))
    instruments = {item.instrument_id: item for item in repository.list_instruments()}
    bars_by_symbol: dict[str, tuple[SignalBar, ...]] = {}
    for instrument_id in snapshot_ids:
        instrument = instruments.get(instrument_id)
        if instrument is None:
            continue
        bars = repository.get_daily_bars(instrument_id, adjustment="split")
        bars_by_symbol[instrument.canonical_symbol] = tuple(
            SignalBar(
                symbol=instrument.canonical_symbol,
                session_date=bar.session_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                dollar_volume=bar.dollar_volume,
            )
            for bar in bars
            if bar.session_date <= session_date
        )
    cutoff = datetime.now(UTC)
    view = PointInTimeDataView(
        bars_by_symbol,
        cutoff_session=session_date,
        cutoff_at=cutoff,
        universe_snapshot_id=f"universe:{session_date.isoformat()}",
    )
    strategies = load_strategies(
        settings.strategy_parameters_file,
        expected_version=settings.strategy_config_version,
    )
    registry = SubscriberRegistry.load(settings.subscriber_file)
    repository.sync_subscribers(registry.subscribers)
    renderer = EmailRenderer(template_version=settings.email_template_version)
    delivery = _delivery_service(settings, repository)
    runner = StrategyRunner()
    summaries: list[dict[str, object]] = []
    failures = 0
    for strategy_number, strategy in enumerate(strategies, start=1):
        progress(
            ProgressEvent(
                f"Evaluating strategy {strategy.strategy_id}",
                completed=strategy_number,
                total=len(strategies),
                unit="strategies",
            )
        )
        run_key = _strategy_run_key(strategy.strategy_id, strategy.version, settings, session_date)
        if repository.strategy_run_exists(run_key) and not args.reprocess:
            summaries.append({"strategy": strategy.strategy_id, "status": "skipped_existing"})
            continue
        result = runner.run(strategy, view)
        if not args.dry_run:
            repository.save_strategy_result(run_key, settings.strategy_config_version, result)
        category = f"strategy:{strategy.strategy_id}"
        recipients = registry.recipients(category)
        if not recipients:
            failures += 1
        items = tuple(
            {
                "symbol": selection.symbol,
                "company": _company_name(selection.symbol, instruments),
                "rank": selection.rank,
                "conditions": ", ".join(selection.matched_conditions),
                "metrics": json.dumps(selection.metrics, sort_keys=True, default=str),
            }
            for selection in result.selections
        )
        links = tuple(
            settings.security_link_template.format(symbol=quote(selection.symbol, safe=""))
            for selection in result.selections
        )
        rendered = renderer.render(
            NotificationContent(
                category=category,
                title=f"{strategy.strategy_id} {session_date.isoformat()}",
                occurred_on=session_date,
                cutoff_at=cutoff,
                status=result.status.value,
                summary=(
                    f"Strategy version {strategy.version}; configuration "
                    f"{settings.strategy_config_version}; {len(result.selections)} selections; "
                    f"{result.symbols_evaluated} evaluated; "
                    f"{result.symbols_skipped} skipped."
                ),
                items=items,
                source_links=links,
                revision=run_key,
            )
        )
        delivery_result = delivery.deliver(
            rendered,
            recipients=recipients,
            dry_run=args.dry_run or (args.reprocess and not getattr(args, "allow_retry", False)),
        )
        if result.error or delivery_result.failed:
            failures += 1
        summaries.append(
            {
                "strategy": strategy.strategy_id,
                "status": result.status.value,
                "selections": len(result.selections),
                "symbols_evaluated": result.symbols_evaluated,
                "symbols_skipped": result.symbols_skipped,
                "sent": len(delivery_result.succeeded),
                "failed_recipients": len(delivery_result.failed),
                "dry_run": len(delivery_result.dry_run),
            }
        )
    print(
        json.dumps({"status": "complete" if not failures else "partial", "strategies": summaries})
    )
    return 0 if not failures else 4


def retry_notifications(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Re-evaluate a session and retry only recipients lacking successful keys."""
    retry_args = argparse.Namespace(
        session=args.session,
        dry_run=args.dry_run,
        reprocess=True,
        allow_retry=True,
    )
    return screen(retry_args, settings, repository, progress)


def health_check(
    args: argparse.Namespace, settings: AppSettings, repository: SQLiteRepository
) -> int:
    """Evaluate persisted schedules plus database, disk, market, and SMTP dependencies."""
    del args
    now = datetime.now(UTC)
    checks: list[dict[str, object]] = []

    try:
        integrity_ok = repository.database_integrity()
    except (OSError, RuntimeError):
        integrity_ok = False
    checks.append({"name": "database_integrity", "status": "pass" if integrity_ok else "fail"})

    try:
        free_bytes = shutil.disk_usage(repository.path.parent).free
        disk_ok = free_bytes >= settings.health_min_free_disk_bytes
    except OSError:
        free_bytes = None
        disk_ok = False
    checks.append(
        {
            "name": "disk_space",
            "status": "pass" if disk_ok else "fail",
            "free_bytes": free_bytes,
            "minimum_bytes": settings.health_min_free_disk_bytes,
        }
    )

    try:
        client = build_alpaca(settings)
        lookback_days = max(30, settings.health_market_max_age_sessions * 4 + 14)
        sessions = client.fetch_calendar(
            start=now.date() - timedelta(days=lookback_days), end=now.date()
        )
        completed = ExchangeCalendar(
            finalization_delay=timedelta(minutes=settings.finalization_delay_minutes)
        ).latest_completed_session(sessions, now=now)
        stored_session = repository.latest_daily_session(adjustment="split")
        if completed is None or stored_session is None:
            market_lag = None
            market_ok = False
        else:
            market_lag = sum(
                stored_session < session.session_date <= completed.session_date
                for session in sessions
            )
            market_ok = market_lag <= settings.health_market_max_age_sessions
    except Exception:
        completed = None
        stored_session = None
        market_lag = None
        market_ok = False
    checks.append(
        {
            "name": "market_data",
            "status": "pass" if market_ok else "fail",
            "stored_session": stored_session.isoformat() if stored_session else None,
            "latest_completed_session": (completed.session_date.isoformat() if completed else None),
            "lag_sessions": market_lag,
            "maximum_lag_sessions": settings.health_market_max_age_sessions,
        }
    )

    try:
        _smtp_transport(settings).probe()
        smtp_ok = True
    except Exception:
        smtp_ok = False
    checks.append({"name": "smtp_transport", "status": "pass" if smtp_ok else "fail"})

    for operation in settings.health_required_operations:
        run = repository.latest_operation_run(operation)
        age_hours: float | None = None
        schedule_ok = False
        if run is not None and run["status"] == "complete" and run["finished_at"] is not None:
            finished_at = datetime.fromisoformat(str(run["finished_at"]))
            age_hours = (now - finished_at.astimezone(UTC)).total_seconds() / 3600
            schedule_ok = age_hours <= settings.health_operation_max_age_hours
        checks.append(
            {
                "name": f"schedule:{operation}",
                "status": "pass" if schedule_ok else "fail",
                "last_status": run["status"] if run else None,
                "age_hours": age_hours,
                "maximum_age_hours": settings.health_operation_max_age_hours,
            }
        )

    healthy = all(check["status"] == "pass" for check in checks)
    print(
        json.dumps(
            {"status": "healthy" if healthy else "unhealthy", "checks": checks},
            sort_keys=True,
        )
    )
    return 0 if healthy else 4


def data_audit(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Persist and summarize canonical data-quality findings under the global lock."""
    del args, settings
    new_findings = repository.run_data_audit(progress)
    findings = repository.count("quarantined_records")
    print(
        json.dumps(
            {
                "status": "complete",
                "new_quality_findings": new_findings,
                "quality_findings": findings,
            }
        )
    )
    return 0


def run_backtest(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Execute a validated single-segment or walk-forward historical evaluation."""
    progress(ProgressEvent("Loading the backtest request and stored inputs"))
    request = parse_backtest_request(json.loads(args.request.read_text(encoding="utf-8")))
    if request.version != settings.backtest_config_version:
        raise ValueError("backtest request version does not match runtime configuration")
    strategies = load_strategies(
        settings.strategy_parameters_file,
        expected_version=settings.strategy_config_version,
    )
    enabled_strategy = next(
        (candidate for candidate in strategies if candidate.strategy_id == request.strategy_id),
        None,
    )
    if enabled_strategy is None:
        raise ValueError(f"backtest strategy is not enabled: {request.strategy_id}")
    bars_by_symbol, snapshots, dividends = _backtest_inputs(repository, request.configuration)

    if isinstance(request, SingleBacktestRequest):
        result = BacktestEngine().run(
            enabled_strategy,
            bars_by_symbol,
            snapshots,
            request.configuration,
            cash_dividends=dividends,
            progress=_backtest_progress(progress),
        )
        paths = write_backtest_artifacts(result, request.output_directory)
        summary: dict[str, object] = {
            "status": "complete",
            "mode": request.mode,
            "strategy": request.strategy_id,
            "trades": len(result.trades),
            "folds": 0,
            "output_directory": str(request.output_directory.resolve()),
            "artifacts": {key: str(path) for key, path in paths.items()},
        }
    elif isinstance(request, WalkForwardBacktestRequest):
        candidates = tuple(
            build_strategy(request.strategy_id, parameters)
            for parameters in request.candidate_parameters
        )
        experiment = WalkForwardExperiment(
            engine=BacktestEngine(),
            selector=request.selection,
            progress=_backtest_progress(progress),
        ).run(
            candidates=candidates,
            sessions=sorted(
                {
                    bar.session_date
                    for symbol, bars in bars_by_symbol.items()
                    if symbol != request.configuration.benchmark_symbol
                    for bar in bars
                }
            ),
            bars_by_symbol=bars_by_symbol,
            universe_snapshots=snapshots,
            base_config=request.configuration,
            walk_forward=request.walk_forward,
            cash_dividends=dividends,
        )
        paths = write_walk_forward_artifacts(experiment, request.output_directory)
        summary = {
            "status": "complete",
            "mode": request.mode,
            "strategy": request.strategy_id,
            "trades": sum(len(fold.out_of_sample.trades) for fold in experiment.folds),
            "folds": len(experiment.folds),
            "output_directory": str(request.output_directory.resolve()),
            "artifacts": {key: str(path) for key, path in paths.items()},
        }
    else:  # pragma: no cover - discriminated validation makes this unreachable.
        raise TypeError("unsupported backtest request")
    print(json.dumps(summary, sort_keys=True))
    return 0


def _backtest_inputs(repository: SQLiteRepository, config: BacktestConfig) -> tuple[
    dict[str, tuple[SignalBar, ...]],
    dict[date, frozenset[str]],
    Mapping[tuple[str, date], Decimal],
]:
    """Load one bounded, adjustment-aware dataset for historical evaluation."""
    bars_by_symbol: dict[str, tuple[SignalBar, ...]] = {}
    for instrument in repository.list_instruments():
        bars = repository.get_daily_bars(instrument.instrument_id, adjustment="split")
        total_return_by_session = {
            bar.session_date: bar.close
            for bar in repository.get_daily_bars(instrument.instrument_id, adjustment="all")
        }
        bars_by_symbol[instrument.canonical_symbol] = tuple(
            SignalBar(
                symbol=instrument.canonical_symbol,
                session_date=bar.session_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                dollar_volume=bar.dollar_volume,
                total_return_close=total_return_by_session.get(bar.session_date),
            )
            for bar in bars
            if config.start <= bar.session_date <= config.end
        )
    return (
        bars_by_symbol,
        repository.universe_snapshots(start=config.start, end=config.end),
        repository.cash_dividends(start=config.start, end=config.end),
    )


def _backtest_progress(reporter: ProgressReporter) -> Callable[[BacktestProgress], None]:
    """Adapt engine session completions to bounded operation progress milestones."""

    def report(event: BacktestProgress) -> None:
        if event.completed_sessions % 25 and event.completed_sessions != event.total_sessions:
            return
        reporter(
            ProgressEvent(
                "Evaluating backtest sessions "
                f"({event.evaluation_segment}, {event.session_date.isoformat()})",
                completed=event.completed_sessions,
                total=event.total_sessions,
                unit="sessions",
            )
        )

    return report


def sec_sync(
    args: argparse.Namespace,
    settings: AppSettings,
    repository: SQLiteRepository,
    progress: ProgressReporter = no_progress,
) -> int:
    """Discover, classify, and deliver every configured candidate SEC filing."""
    rules = _load_string_lists(settings.sec_classification_rules_file)
    client = EdgarClient(
        organization=settings.sec_organization,
        contact_email=settings.sec_contact_email,
        candidate_forms=frozenset(settings.sec_candidate_forms),
        retry_policy=_retry_policy(settings),
    )
    classifier = FilingClassifier(rules=rules)
    progress(ProgressEvent("Loading SEC issuer references"))
    issuer_mapping = client.fetch_company_tickers()
    registry = SubscriberRegistry.load(settings.subscriber_file)
    repository.sync_subscribers(registry.subscribers)
    renderer = EmailRenderer(template_version=settings.email_template_version)
    delivery = _delivery_service(settings, repository)
    eligible_types = {"common_stock", "adr"}
    discovered = delivered = skipped = failures = 0
    eligible = tuple(
        instrument
        for instrument in repository.list_instruments()
        if instrument.instrument_type.value in eligible_types
    )
    if not eligible:
        progress(ProgressEvent("No eligible SEC issuers found"))
    for issuer_number, instrument in enumerate(eligible, start=1):
        if should_report(issuer_number, total=len(eligible), every=100):
            progress(
                ProgressEvent(
                    "Checking eligible SEC issuers",
                    completed=issuer_number,
                    total=len(eligible),
                    unit="issuers",
                )
            )
        cik = issuer_mapping.get(instrument.provider_symbol)
        if cik is None:
            continue
        try:
            last_successful_date = repository.latest_sec_filing_date(cik)
            filing_date_from = max(
                settings.sec_history_start,
                last_successful_date or settings.sec_history_start,
            )
            filings = client.discover_filings(
                cik=cik,
                symbol=instrument.canonical_symbol,
                filing_date_from=filing_date_from,
            )
            for filing in filings:
                discovered += 1
                if repository.sec_filing_exists(filing.accession_number):
                    skipped += 1
                    continue
                classification = classifier.classify(client.fetch_document_text(filing))
                recipients = tuple(
                    sorted(
                        set(registry.recipients("sec"))
                        | set(registry.recipients(f"sec:{classification.classification}"))
                    )
                )
                if not recipients:
                    failures += 1
                    continue
                rendered = renderer.render(
                    NotificationContent(
                        category="sec",
                        title=f"{filing.symbol} {filing.form} offering filing",
                        occurred_on=filing.filing_date,
                        cutoff_at=filing.accepted_at,
                        status="uncertain" if classification.uncertain else "complete",
                        summary=(
                            f"{filing.issuer}; classification {classification.classification}; "
                            f"confidence {classification.confidence}; {classification.reason}."
                        ),
                        items=(
                            {
                                "symbol": filing.symbol,
                                "issuer": filing.issuer,
                                "form": filing.form,
                                "classification": classification.classification,
                                "uncertain": classification.uncertain,
                                "accepted_at": filing.accepted_at.isoformat(),
                                "evidence": ", ".join(classification.matched_evidence),
                            },
                        ),
                        source_links=(filing.source_url,),
                        revision="1",
                    )
                )
                delivery_result = delivery.deliver(
                    rendered, recipients=recipients, dry_run=args.dry_run
                )
                if delivery_result.failed:
                    failures += 1
                    continue
                if not args.dry_run:
                    repository.save_sec_filing(filing, classification)
                delivered += len(delivery_result.succeeded)
        except Exception as exc:
            failures += 1
            repository.quarantine(
                source="sec",
                record_type="issuer_sync",
                reason="issuer filing synchronization failed",
                payload={
                    "symbol": instrument.provider_symbol,
                    "cik": cik,
                    "error_type": type(exc).__name__,
                },
            )
            continue
    print(
        json.dumps(
            {
                "status": "complete" if not failures else "partial",
                "filings_discovered": discovered,
                "deliveries": delivered,
                "already_processed": skipped,
                "failures": failures,
            },
            sort_keys=True,
        )
    )
    return 0 if not failures else 4


def _sync_range(
    client: AlpacaClient,
    settings: AppSettings,
    repository: SQLiteRepository,
    start: date,
    end: date,
    *,
    action_start: date,
    progress: ProgressReporter,
) -> int:
    instruments = repository.list_instruments()
    if not instruments:
        print('{"status":"failed","reason":"universe is empty; run sync-universe first"}')
        return 4
    progress(ProgressEvent("Resolving completed exchange sessions"))
    sessions = tuple(
        session.session_date for session in client.fetch_calendar(start=start, end=end)
    )
    identities = {
        instrument.provider_symbol: instrument.instrument_id for instrument in instruments
    }
    summaries: list[dict[str, object]] = []
    exit_code = 0
    for adjustment in ("raw", "split", "all"):
        progress(ProgressEvent(f"Synchronizing {adjustment} daily-bar series"))
        service = MarketDataIngestionService(
            provider=client,
            repository=repository,
            provider_name="alpaca",
            feed=settings.alpaca_feed,
            chunk_size=settings.ingestion_chunk_size,
            adjustment=adjustment,
            progress=progress,
        )
        result = service.sync(
            symbol_identities=identities,
            start=start,
            end=end,
            expected_sessions=sessions,
        )
        summaries.append(
            {
                "adjustment": adjustment,
                "status": result.status,
                "rows": result.rows_received,
                "failures": result.failures,
                "quality_findings": result.quality_findings,
                "chunks_skipped": result.chunks_skipped,
            }
        )
        if result.status != "complete":
            exit_code = 4
    symbols = sorted(identities)
    action_count = 0
    progress(ProgressEvent("Refreshing corporate actions"))
    total_chunks = (
        len(symbols) + settings.ingestion_chunk_size - 1
    ) // settings.ingestion_chunk_size
    for chunk_number, offset in enumerate(
        range(0, len(symbols), settings.ingestion_chunk_size), start=1
    ):
        chunk = symbols[offset : offset + settings.ingestion_chunk_size]
        progress(
            ProgressEvent(
                "Fetching corporate actions",
                completed=chunk_number,
                total=total_chunks,
                unit="chunks",
            )
        )
        actions = list(client.fetch_corporate_actions(start=action_start, end=end, symbols=chunk))
        repository.upsert_corporate_actions(actions)
        action_count += len(actions)
    print(
        json.dumps(
            {
                "status": "complete" if exit_code == 0 else "partial",
                "series": summaries,
                "corporate_actions": action_count,
            }
        )
    )
    return exit_code


def _latest_completed_session(client: AlpacaClient, settings: AppSettings) -> MarketSession:
    today = datetime.now(UTC).date()
    sessions = client.fetch_calendar(start=today - timedelta(days=14), end=today)
    calendar = ExchangeCalendar(
        finalization_delay=timedelta(minutes=settings.finalization_delay_minutes)
    )
    latest = calendar.latest_completed_session(sessions, now=datetime.now(UTC))
    if latest is None:
        raise RuntimeError("no completed exchange session is available")
    return latest


def _session_lookback_start(client: AlpacaClient, end: date, count: int) -> date:
    """Resolve an exact trading-session lookback using the provider calendar."""
    calendar_start = end - timedelta(days=max(14, count * 3))
    sessions = sorted(
        {
            session.session_date
            for session in client.fetch_calendar(start=calendar_start, end=end)
            if session.session_date <= end
        }
    )
    if len(sessions) < count:
        raise RuntimeError(
            f"provider calendar returned {len(sessions)} sessions; {count} are required"
        )
    return sessions[-count]


def _retry_policy(settings: AppSettings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.http_max_attempts,
        base_delay_seconds=settings.http_base_delay_seconds,
        max_delay_seconds=settings.http_max_delay_seconds,
        jitter_seconds=settings.http_jitter_seconds,
    )


def _delivery_service(settings: AppSettings, repository: SQLiteRepository) -> EmailDeliveryService:
    transport = _smtp_transport(settings)
    assert settings.smtp_from_address is not None
    return EmailDeliveryService(
        repository=repository,
        transport=transport,
        sender=settings.smtp_from_address,
        max_attempts=settings.email_max_attempts,
        backoff_seconds=settings.email_backoff_seconds,
        daily_send_limit=settings.email_daily_send_limit,
    )


def _smtp_transport(settings: AppSettings) -> SMTPTransport:
    """Build the configured SMTP adapter for delivery and health probes."""
    assert settings.smtp_host is not None
    assert settings.smtp_port is not None
    assert settings.smtp_security is not None
    return SMTPTransport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=(settings.smtp_username.get_secret_value() if settings.smtp_username else None),
        password=(settings.smtp_password.get_secret_value() if settings.smtp_password else None),
        use_ssl=settings.smtp_security == "ssl",
        starttls=settings.smtp_security == "starttls",
    )


def _strategy_run_key(
    strategy_id: str, strategy_version: str, settings: AppSettings, session_date: date
) -> str:
    material = "\0".join(
        (
            strategy_id,
            strategy_version,
            settings.strategy_config_version,
            session_date.isoformat(),
        )
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _company_name(symbol: str, instruments: Mapping[str, object]) -> str:
    for candidate in instruments.values():
        if getattr(candidate, "canonical_symbol", None) == symbol:
            return str(getattr(candidate, "name", symbol))
    return symbol


def _load_string_lists(path: Path) -> Mapping[str, tuple[str, ...]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("SEC rules file must contain an object")
    result: dict[str, tuple[str, ...]] = {}
    for category, patterns in parsed.items():
        if (
            not isinstance(category, str)
            or not isinstance(patterns, list)
            or any(not isinstance(pattern, str) or not pattern for pattern in patterns)
        ):
            raise ValueError("SEC classification rules are invalid")
        result[category] = tuple(patterns)
    return result
