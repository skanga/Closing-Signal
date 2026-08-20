"""Resumable EOD ingestion orchestration over provider and repository boundaries."""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

from closing_signal.core.progress import ProgressEvent, ProgressReporter, no_progress
from closing_signal.domain.models import DailyBar


class DailyBarProvider(Protocol):
    def fetch_daily_bars(
        self, *, symbols: list[str], start: date, end: date, adjustment: str = "raw"
    ) -> Iterable[DailyBar]: ...


class IngestionRepository(Protocol):
    def begin_ingestion_run(
        self,
        *,
        run_id: str,
        provider: str,
        feed: str,
        start: date,
        end: date,
        symbols_requested: int,
    ) -> None: ...

    def completed_ingestion_pages(self, run_id: str) -> frozenset[str]: ...

    def record_ingestion_page(
        self,
        *,
        run_id: str,
        page_key: str,
        status: str,
        rows_received: int,
        error_type: str | None,
    ) -> None: ...

    def upsert_daily_bars(self, bars: Iterable[DailyBar]) -> None: ...

    def quarantine(
        self,
        *,
        source: str,
        record_type: str,
        reason: str,
        payload: dict[str, object],
    ) -> str: ...

    def finish_ingestion_run(
        self, *, run_id: str, rows_received: int, failures: int, quality_findings: int
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class IngestionResult:
    run_id: str
    status: str
    rows_received: int
    failures: int
    quality_findings: int
    chunks_skipped: int


class MarketDataIngestionService:
    """Ingest stable symbol chunks and resume only incomplete chunks."""

    def __init__(
        self,
        *,
        provider: DailyBarProvider,
        repository: IngestionRepository,
        provider_name: str,
        feed: str,
        chunk_size: int,
        adjustment: str = "raw",
        progress: ProgressReporter = no_progress,
    ) -> None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        self.provider = provider
        self.repository = repository
        self.provider_name = provider_name
        self.feed = feed
        self.chunk_size = chunk_size
        if adjustment not in {"raw", "split", "all"}:
            raise ValueError("adjustment must be raw, split, or all")
        self.adjustment = adjustment
        self.progress = progress

    def sync(
        self,
        *,
        symbol_identities: Mapping[str, str],
        start: date,
        end: date,
        expected_sessions: tuple[date, ...],
    ) -> IngestionResult:
        """Synchronize, quarantine findings, and preserve successful progress."""
        if end < start:
            raise ValueError("ingestion end cannot precede start")
        symbols = sorted(symbol_identities)
        run_feed = f"{self.feed}/{self.adjustment}"
        run_id = _run_id(self.provider_name, run_feed, start, end, symbols)
        self.repository.begin_ingestion_run(
            run_id=run_id,
            provider=self.provider_name,
            feed=run_feed,
            start=start,
            end=end,
            symbols_requested=len(symbols),
        )
        completed = self.repository.completed_ingestion_pages(run_id)
        rows_received = failures = findings = skipped = 0
        total_chunks = (len(symbols) + self.chunk_size - 1) // self.chunk_size
        for chunk_number, offset in enumerate(range(0, len(symbols), self.chunk_size), start=1):
            chunk = symbols[offset : offset + self.chunk_size]
            self.progress(
                ProgressEvent(
                    f"Fetching {self.adjustment} daily bars",
                    completed=chunk_number,
                    total=total_chunks,
                    unit="chunks",
                )
            )
            page_key = hashlib.sha256("\0".join(chunk).encode()).hexdigest()
            if page_key in completed:
                skipped += 1
                continue
            try:
                fetched = list(
                    self.provider.fetch_daily_bars(
                        symbols=chunk,
                        start=start,
                        end=end,
                        adjustment=self.adjustment,
                    )
                )
            except Exception as exc:
                failures += 1
                findings += 1
                self.repository.quarantine(
                    source=self.provider_name,
                    record_type="provider_page",
                    reason="provider page could not be validated or completed",
                    payload={
                        "symbols": chunk,
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "adjustment": self.adjustment,
                        "error_type": type(exc).__name__,
                    },
                )
                self.repository.record_ingestion_page(
                    run_id=run_id,
                    page_key=page_key,
                    status="failed",
                    rows_received=0,
                    error_type=type(exc).__name__,
                )
                continue
            accepted: list[DailyBar] = []
            observed: set[tuple[str, date]] = set()
            for bar in fetched:
                identity = symbol_identities.get(bar.instrument_id)
                key = (bar.instrument_id, bar.session_date)
                if identity is None:
                    findings += 1
                    self.repository.quarantine(
                        source=self.provider_name,
                        record_type="daily_bar",
                        reason="provider symbol has no canonical identity",
                        payload={
                            "symbol": bar.instrument_id,
                            "session_date": bar.session_date.isoformat(),
                        },
                    )
                    continue
                if key in observed:
                    findings += 1
                    self.repository.quarantine(
                        source=self.provider_name,
                        record_type="daily_bar",
                        reason="duplicate bar in provider response",
                        payload={"symbol": key[0], "session_date": key[1].isoformat()},
                    )
                    continue
                observed.add(key)
                accepted.append(replace(bar, instrument_id=identity))
            expected = set(expected_sessions)
            for symbol in chunk:
                actual = {
                    session for observed_symbol, session in observed if observed_symbol == symbol
                }
                for missing in sorted(expected - actual):
                    findings += 1
                    self.repository.quarantine(
                        source=self.provider_name,
                        record_type="missing_daily_bar",
                        reason="eligible symbol missing expected session",
                        payload={"symbol": symbol, "session_date": missing.isoformat()},
                    )
            self.repository.upsert_daily_bars(accepted)
            rows_received += len(accepted)
            self.repository.record_ingestion_page(
                run_id=run_id,
                page_key=page_key,
                status="complete",
                rows_received=len(accepted),
                error_type=None,
            )
        status = self.repository.finish_ingestion_run(
            run_id=run_id,
            rows_received=rows_received,
            failures=failures,
            quality_findings=findings,
        )
        return IngestionResult(run_id, status, rows_received, failures, findings, skipped)


def _run_id(provider: str, feed: str, start: date, end: date, symbols: list[str]) -> str:
    material = "\0".join((provider, feed, start.isoformat(), end.isoformat(), *symbols)).encode()
    return hashlib.sha256(material).hexdigest()
