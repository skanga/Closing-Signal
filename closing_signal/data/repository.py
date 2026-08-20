"""Canonical persistence adapter with idempotent SQLite writes."""

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from closing_signal.core.progress import ProgressEvent, ProgressReporter, no_progress
from closing_signal.domain.models import (
    CorporateAction,
    DailyBar,
    Exchange,
    Instrument,
    InstrumentType,
)

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    canonical_symbol TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    status TEXT NOT NULL,
    tradable INTEGER NOT NULL,
    first_observed TEXT NOT NULL,
    last_observed TEXT NOT NULL
);
DROP INDEX IF EXISTS idx_instrument_provider_symbol;
CREATE INDEX IF NOT EXISTS idx_instrument_provider_symbol ON instruments(provider_symbol);
CREATE TABLE IF NOT EXISTS instrument_symbols (
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    provider TEXT NOT NULL,
    symbol TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    PRIMARY KEY (instrument_id, provider, symbol, valid_from)
);
CREATE TABLE IF NOT EXISTS universe_snapshots (
    session_date TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS universe_snapshot_members (
    session_date TEXT NOT NULL REFERENCES universe_snapshots(session_date) ON DELETE CASCADE,
    instrument_id TEXT NOT NULL REFERENCES instruments(instrument_id),
    PRIMARY KEY (session_date, instrument_id)
);
CREATE TABLE IF NOT EXISTS daily_bars (
    instrument_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    source_timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    feed TEXT NOT NULL,
    frequency TEXT NOT NULL,
    open TEXT NOT NULL,
    high TEXT NOT NULL,
    low TEXT NOT NULL,
    close TEXT NOT NULL,
    volume INTEGER NOT NULL,
    dollar_volume TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    PRIMARY KEY (instrument_id, session_date, provider, feed, frequency, adjustment)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    provider TEXT NOT NULL,
    provider_action_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    effective_date TEXT,
    process_date TEXT,
    ratio TEXT,
    cash_amount TEXT,
    new_symbol TEXT,
    source_payload TEXT NOT NULL,
    PRIMARY KEY (provider, provider_action_id)
);
CREATE TABLE IF NOT EXISTS quarantined_records (
    quarantine_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    record_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
    delivery_key TEXT PRIMARY KEY,
    recipient TEXT NOT NULL,
    template_version TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_response TEXT,
    first_attempt_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_key TEXT NOT NULL REFERENCES notification_deliveries(delivery_key),
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_response TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    feed TEXT NOT NULL,
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    symbols_requested INTEGER NOT NULL,
    status TEXT NOT NULL,
    rows_received INTEGER NOT NULL,
    failures INTEGER NOT NULL,
    quality_findings INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_pages (
    run_id TEXT NOT NULL REFERENCES ingestion_runs(run_id),
    page_key TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_received INTEGER NOT NULL,
    error_type TEXT,
    attempts INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, page_key)
);
CREATE TABLE IF NOT EXISTS strategy_runs (
    run_key TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    config_version TEXT NOT NULL,
    universe_snapshot_id TEXT NOT NULL,
    session_date TEXT NOT NULL,
    input_cutoff TEXT NOT NULL,
    status TEXT NOT NULL,
    parameters TEXT NOT NULL,
    symbols_evaluated INTEGER NOT NULL,
    symbols_skipped INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS strategy_selections (
    run_key TEXT NOT NULL REFERENCES strategy_runs(run_key) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    rank INTEGER NOT NULL,
    matched_conditions TEXT NOT NULL,
    metrics TEXT NOT NULL,
    PRIMARY KEY (run_key, symbol)
);
CREATE TABLE IF NOT EXISTS sec_filings (
    accession_number TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    issuer TEXT NOT NULL,
    symbol TEXT NOT NULL,
    form TEXT NOT NULL,
    filing_date TEXT NOT NULL,
    accepted_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence TEXT NOT NULL,
    uncertain INTEGER NOT NULL,
    matched_evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    revision INTEGER NOT NULL,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_locks (
    operation TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_runs (
    run_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    exit_code INTEGER,
    error_type TEXT
);
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    active INTEGER NOT NULL,
    categories TEXT NOT NULL,
    consent_source TEXT,
    policy_version TEXT,
    consented_at TEXT,
    confirmed_at TEXT,
    deactivated_at TEXT,
    deactivation_reason TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscription_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT NOT NULL,
    changed_at TEXT NOT NULL
);
"""


class SQLiteRepository:
    """Local SQLite implementation of canonical storage boundaries.

    SQLite is an adapter for the confirmed local-machine MVP, not a commitment to
    the still-unresolved production database technology.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(_SCHEMA)
            self._migrate_daily_bar_adjustment_key(connection)
            self._migrate_strategy_run_diagnostics(connection)
            self._migrate_subscriber_consent(connection)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (5, datetime.now().astimezone().isoformat()),
            )

    @staticmethod
    def _migrate_daily_bar_adjustment_key(connection: sqlite3.Connection) -> None:
        """Upgrade pre-v3 canonical bar tables without losing their raw rows."""
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(daily_bars)").fetchall()
        }
        if "adjustment" in columns:
            return
        connection.executescript(
            """
            ALTER TABLE daily_bars RENAME TO daily_bars_pre_v3;
            CREATE TABLE daily_bars (
                instrument_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                source_timestamp TEXT NOT NULL,
                provider TEXT NOT NULL,
                feed TEXT NOT NULL,
                frequency TEXT NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume INTEGER NOT NULL,
                dollar_volume TEXT NOT NULL,
                adjustment TEXT NOT NULL,
                PRIMARY KEY (
                    instrument_id, session_date, provider, feed, frequency, adjustment
                )
            );
            INSERT INTO daily_bars
            SELECT instrument_id, session_date, source_timestamp, provider, feed, frequency,
                   open, high, low, close, volume, dollar_volume, 'raw'
            FROM daily_bars_pre_v3;
            DROP TABLE daily_bars_pre_v3;
            """
        )

    @staticmethod
    def _migrate_strategy_run_diagnostics(connection: sqlite3.Connection) -> None:
        """Add explicit incomplete-history counts to pre-diagnostic databases."""
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(strategy_runs)").fetchall()
        }
        if "symbols_skipped" not in columns:
            connection.execute(
                "ALTER TABLE strategy_runs ADD COLUMN symbols_skipped INTEGER NOT NULL DEFAULT 0"
            )

    @staticmethod
    def _migrate_subscriber_consent(connection: sqlite3.Connection) -> None:
        """Add double-opt-in and deactivation evidence to pre-v5 databases."""
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(subscribers)").fetchall()
        }
        additions = {
            "consent_source": "TEXT",
            "policy_version": "TEXT",
            "consented_at": "TEXT",
            "confirmed_at": "TEXT",
            "deactivated_at": "TEXT",
            "deactivation_reason": "TEXT",
        }
        for column, column_type in additions.items():
            if column not in columns:
                connection.execute(f"ALTER TABLE subscribers ADD COLUMN {column} {column_type}")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def upsert_instruments(self, instruments: Iterable[Instrument]) -> None:
        """Insert current metadata without rewriting the first-observed identity."""
        rows = [
            (
                item.instrument_id,
                item.canonical_symbol,
                item.provider_symbol,
                item.name,
                item.exchange.value,
                item.instrument_type.value,
                item.status,
                int(item.tradable),
                item.first_observed.isoformat(),
                item.last_observed.isoformat(),
            )
            for item in instruments
        ]
        with closing(self._connect()) as connection, connection:
            for row in rows:
                existing = connection.execute(
                    "SELECT provider_symbol FROM instruments WHERE instrument_id = ?", (row[0],)
                ).fetchone()
                if existing and existing[0] != row[2]:
                    connection.execute(
                        """
                        UPDATE instrument_symbols SET valid_to = ?
                        WHERE instrument_id = ? AND provider = 'alpaca' AND valid_to IS NULL
                        """,
                        (row[9], row[0]),
                    )
                connection.execute(
                    """
                    INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instrument_id) DO UPDATE SET
                        canonical_symbol=excluded.canonical_symbol,
                        provider_symbol=excluded.provider_symbol,
                        name=excluded.name,
                        exchange=excluded.exchange,
                        instrument_type=excluded.instrument_type,
                        status=excluded.status,
                        tradable=excluded.tradable,
                        first_observed=MIN(instruments.first_observed, excluded.first_observed),
                        last_observed=MAX(instruments.last_observed, excluded.last_observed)
                    """,
                    row,
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO instrument_symbols
                        (instrument_id, provider, symbol, valid_from, valid_to)
                    VALUES (?, 'alpaca', ?, ?, NULL)
                    """,
                    (row[0], row[2], row[9] if existing and existing[0] != row[2] else row[8]),
                )

    def upsert_daily_bars(self, bars: Iterable[DailyBar]) -> None:
        """Upsert raw bars by stable provider/feed/frequency identity."""
        rows = [
            (
                bar.instrument_id,
                bar.session_date.isoformat(),
                bar.source_timestamp.isoformat(),
                bar.provider,
                bar.feed,
                bar.frequency,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                bar.volume,
                str(bar.dollar_volume),
                bar.adjustment,
            )
            for bar in bars
        ]
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id, session_date, provider, feed, frequency, adjustment)
                DO UPDATE SET
                    source_timestamp=excluded.source_timestamp,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    dollar_volume=excluded.dollar_volume
                """,
                rows,
            )

    def upsert_corporate_actions(self, actions: Iterable[CorporateAction]) -> None:
        """Upsert actions by stable provider ID while retaining source payload."""
        rows = [
            (
                action.provider,
                action.provider_action_id,
                action.action_type,
                action.provider_symbol,
                action.effective_date.isoformat() if action.effective_date else None,
                action.process_date.isoformat() if action.process_date else None,
                str(action.ratio) if action.ratio is not None else None,
                str(action.cash_amount) if action.cash_amount is not None else None,
                action.new_symbol,
                json.dumps(action.source_payload, sort_keys=True, separators=(",", ":")),
            )
            for action in actions
        ]
        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO corporate_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, provider_action_id) DO UPDATE SET
                    action_type=excluded.action_type,
                    provider_symbol=excluded.provider_symbol,
                    effective_date=excluded.effective_date,
                    process_date=excluded.process_date,
                    ratio=excluded.ratio,
                    cash_amount=excluded.cash_amount,
                    new_symbol=excluded.new_symbol,
                    source_payload=excluded.source_payload
                """,
                rows,
            )

    def quarantine(
        self,
        *,
        source: str,
        record_type: str,
        reason: str,
        payload: dict[str, object],
    ) -> str:
        """Store a suspect record once and count identical later observations."""
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        material = f"{source}\0{record_type}\0{reason}\0{encoded}".encode()
        quarantine_id = hashlib.sha256(material).hexdigest()
        observed_at = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO quarantined_records VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(quarantine_id) DO UPDATE SET
                    last_seen_at=excluded.last_seen_at,
                    occurrence_count=quarantined_records.occurrence_count + 1
                """,
                (
                    quarantine_id,
                    source,
                    record_type,
                    reason,
                    encoded,
                    observed_at,
                    observed_at,
                ),
            )
        return quarantine_id

    def notification_status(self, delivery_key: str) -> str | None:
        """Return the current final/intermediate state for an idempotency key."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT status FROM notification_deliveries WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
        return str(row[0]) if row else None

    def notification_attempt_count_since(self, since: datetime) -> int:
        """Count provider send attempts since a timezone-aware instant."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM notification_attempts
                WHERE attempted_at >= ?
                  AND provider_response != 'DailySendLimitExceeded'
                """,
                (since.isoformat(),),
            ).fetchone()
        return int(row[0])

    def record_notification_attempt(
        self,
        *,
        delivery_key: str,
        recipient: str,
        template_version: str,
        status: str,
        provider_response: str | None,
        attempted_at: datetime,
    ) -> None:
        """Atomically persist recipient state and an immutable attempt audit row."""
        timestamp = attempted_at.isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO notification_deliveries VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(delivery_key) DO UPDATE SET
                    status=excluded.status,
                    provider_response=excluded.provider_response,
                    updated_at=excluded.updated_at
                """,
                (
                    delivery_key,
                    recipient,
                    template_version,
                    status,
                    provider_response,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO notification_attempts
                    (delivery_key, attempted_at, status, provider_response)
                VALUES (?, ?, ?, ?)
                """,
                (delivery_key, timestamp, status, provider_response),
            )

    def begin_ingestion_run(
        self,
        *,
        run_id: str,
        provider: str,
        feed: str,
        start: date,
        end: date,
        symbols_requested: int,
    ) -> None:
        """Create or resume a deterministic ingestion request."""
        timestamp = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, ?, ?, 'running', 0, 0, 0, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET status='running', updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    provider,
                    feed,
                    start.isoformat(),
                    end.isoformat(),
                    symbols_requested,
                    timestamp,
                    timestamp,
                ),
            )

    def completed_ingestion_pages(self, run_id: str) -> frozenset[str]:
        """Return immutable chunks already stored successfully."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT page_key FROM ingestion_pages WHERE run_id = ? AND status = 'complete'",
                (run_id,),
            ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def record_ingestion_page(
        self,
        *,
        run_id: str,
        page_key: str,
        status: str,
        rows_received: int,
        error_type: str | None,
    ) -> None:
        """Record chunk completion/failure and increment its attempt counter."""
        timestamp = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO ingestion_pages VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(run_id, page_key) DO UPDATE SET
                    status=excluded.status,
                    rows_received=excluded.rows_received,
                    error_type=excluded.error_type,
                    attempts=ingestion_pages.attempts + 1,
                    updated_at=excluded.updated_at
                """,
                (run_id, page_key, status, rows_received, error_type, timestamp),
            )

    def finish_ingestion_run(
        self, *, run_id: str, rows_received: int, failures: int, quality_findings: int
    ) -> str:
        """Finalize from current chunk state and return complete or partial."""
        with closing(self._connect()) as connection, connection:
            failed_row = connection.execute(
                "SELECT COUNT(*) FROM ingestion_pages WHERE run_id = ? AND status != 'complete'",
                (run_id,),
            ).fetchone()
            status = "partial" if failed_row and int(failed_row[0]) else "complete"
            connection.execute(
                """
                UPDATE ingestion_runs
                SET status=?, rows_received=rows_received + ?, failures=failures + ?,
                    quality_findings=quality_findings + ?, updated_at=?
                WHERE run_id=?
                """,
                (
                    status,
                    rows_received,
                    failures,
                    quality_findings,
                    datetime.now().astimezone().isoformat(),
                    run_id,
                ),
            )
        return status

    def strategy_run_exists(self, run_key: str) -> bool:
        """Return whether a deterministic strategy result has already persisted."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM strategy_runs WHERE run_key = ?", (run_key,)
            ).fetchone()
        return row is not None

    def save_strategy_result(self, run_key: str, config_version: str, result: object) -> None:
        """Persist a typed strategy result and replace selections atomically."""
        from closing_signal.strategy.framework import StrategyResult

        if not isinstance(result, StrategyResult):
            raise TypeError("result must be a StrategyResult")
        encoded_parameters = json.dumps(
            result.parameters, sort_keys=True, separators=(",", ":"), default=str
        )
        timestamp = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO strategy_runs (
                    run_key, strategy_id, strategy_version, config_version,
                    universe_snapshot_id, session_date, input_cutoff, status,
                    parameters, symbols_evaluated, symbols_skipped, error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_key) DO UPDATE SET
                    status=excluded.status,
                    parameters=excluded.parameters,
                    symbols_evaluated=excluded.symbols_evaluated,
                    symbols_skipped=excluded.symbols_skipped,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    run_key,
                    result.strategy_id,
                    result.strategy_version,
                    config_version,
                    result.universe_snapshot_id,
                    result.session_date.isoformat(),
                    result.input_cutoff.isoformat(),
                    result.status.value,
                    encoded_parameters,
                    result.symbols_evaluated,
                    result.symbols_skipped,
                    result.error,
                    timestamp,
                ),
            )
            connection.execute("DELETE FROM strategy_selections WHERE run_key = ?", (run_key,))
            connection.executemany(
                "INSERT INTO strategy_selections VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        run_key,
                        selection.symbol,
                        selection.rank,
                        json.dumps(selection.matched_conditions),
                        json.dumps(selection.metrics, sort_keys=True, default=str),
                    )
                    for selection in result.selections
                ],
            )

    def sec_filing_exists(self, accession_number: str) -> bool:
        """Use the SEC accession number as the discovery/delivery dedupe identity."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM sec_filings WHERE accession_number = ?",
                (accession_number,),
            ).fetchone()
        return row is not None

    def latest_sec_filing_date(self, cik: int) -> date | None:
        """Return the latest successfully persisted filing date for one issuer."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(filing_date) FROM sec_filings WHERE cik = ?", (cik,)
            ).fetchone()
        return date.fromisoformat(str(row[0])) if row and row[0] is not None else None

    def save_sec_filing(self, filing: object, classification: object) -> None:
        """Persist a canonical filing and its current classification revision."""
        from closing_signal.sec.edgar import FilingClassification, SECFiling

        if not isinstance(filing, SECFiling) or not isinstance(
            classification, FilingClassification
        ):
            raise TypeError("invalid SEC filing or classification")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO sec_filings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(accession_number) DO UPDATE SET
                    classification=excluded.classification,
                    confidence=excluded.confidence,
                    uncertain=excluded.uncertain,
                    matched_evidence=excluded.matched_evidence,
                    reason=excluded.reason,
                    revision=sec_filings.revision + 1,
                    processed_at=excluded.processed_at
                """,
                (
                    filing.accession_number,
                    filing.cik,
                    filing.issuer,
                    filing.symbol,
                    filing.form,
                    filing.filing_date.isoformat(),
                    filing.accepted_at.isoformat(),
                    filing.source_url,
                    classification.classification,
                    classification.confidence,
                    int(classification.uncertain),
                    json.dumps(classification.matched_evidence),
                    classification.reason,
                    datetime.now().astimezone().isoformat(),
                ),
            )

    def acquire_operation_lock(self, operation: str, owner: str) -> bool:
        """Atomically prevent overlapping mutating operator commands."""
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO operation_locks VALUES (?, ?, ?)",
                    (operation, owner, datetime.now().astimezone().isoformat()),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def release_operation_lock(self, operation: str, owner: str) -> None:
        """Release only a lock owned by the current invocation."""
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "DELETE FROM operation_locks WHERE operation = ? AND owner = ?",
                (operation, owner),
            )

    def start_operation_run(self, run_id: str, operation: str) -> None:
        """Persist a running state before dispatch so termination remains visible."""
        if not run_id or not operation:
            raise ValueError("run_id and operation are required")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO operation_runs VALUES (?, ?, ?, NULL, 'running', NULL, NULL)",
                (run_id, operation, datetime.now().astimezone().isoformat()),
            )

    def finish_operation_run(
        self,
        run_id: str,
        *,
        status: Literal["complete", "failed"],
        exit_code: int,
        error_type: str | None,
    ) -> None:
        """Finalize an existing operation without erasing its start evidence."""
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE operation_runs
                SET finished_at = ?, status = ?, exit_code = ?, error_type = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (
                    datetime.now().astimezone().isoformat(),
                    status,
                    exit_code,
                    error_type,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("operation run is missing or already finalized")

    def latest_operation_run(self, operation: str) -> dict[str, object] | None:
        """Return the most recently started state for one scheduled operation."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT run_id, operation, started_at, finished_at, status, exit_code, error_type
                FROM operation_runs
                WHERE operation = ?
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (operation,),
            ).fetchone()
        if row is None:
            return None
        keys = (
            "run_id",
            "operation",
            "started_at",
            "finished_at",
            "status",
            "exit_code",
            "error_type",
        )
        return dict(zip(keys, row, strict=True))

    def sync_subscribers(self, subscribers: Iterable[object]) -> None:
        """Synchronize managed subscriber state and append only actual changes."""
        from closing_signal.notify.subscribers import Subscriber

        timestamp = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as connection, connection:
            for subscriber in subscribers:
                if not isinstance(subscriber, Subscriber):
                    raise TypeError("subscriber must be a Subscriber")
                new_state = json.dumps(
                    {
                        "active": subscriber.active,
                        "categories": sorted(subscriber.categories),
                        "consent_source": subscriber.consent_source,
                        "policy_version": subscriber.policy_version,
                        "consented_at": subscriber.consented_at.isoformat(),
                        "confirmed_at": subscriber.confirmed_at.isoformat(),
                        "deactivated_at": (
                            subscriber.deactivated_at.isoformat()
                            if subscriber.deactivated_at
                            else None
                        ),
                        "deactivation_reason": subscriber.deactivation_reason,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                row = connection.execute(
                    """
                    SELECT active, categories, consent_source, policy_version,
                           consented_at, confirmed_at, deactivated_at, deactivation_reason
                    FROM subscribers WHERE email = ?
                    """,
                    (subscriber.email,),
                ).fetchone()
                previous_state = (
                    json.dumps(
                        {
                            "active": bool(row[0]),
                            "categories": json.loads(row[1]),
                            "consent_source": row[2],
                            "policy_version": row[3],
                            "consented_at": row[4],
                            "confirmed_at": row[5],
                            "deactivated_at": row[6],
                            "deactivation_reason": row[7],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    if row
                    else None
                )
                if previous_state == new_state:
                    continue
                connection.execute(
                    """
                    INSERT INTO subscribers (
                        email, active, categories, consent_source, policy_version,
                        consented_at, confirmed_at, deactivated_at, deactivation_reason,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        active=excluded.active,
                        categories=excluded.categories,
                        consent_source=excluded.consent_source,
                        policy_version=excluded.policy_version,
                        consented_at=excluded.consented_at,
                        confirmed_at=excluded.confirmed_at,
                        deactivated_at=excluded.deactivated_at,
                        deactivation_reason=excluded.deactivation_reason,
                        updated_at=excluded.updated_at
                    """,
                    (
                        subscriber.email,
                        int(subscriber.active),
                        json.dumps(sorted(subscriber.categories)),
                        subscriber.consent_source,
                        subscriber.policy_version,
                        subscriber.consented_at.isoformat(),
                        subscriber.confirmed_at.isoformat(),
                        (
                            subscriber.deactivated_at.isoformat()
                            if subscriber.deactivated_at
                            else None
                        ),
                        subscriber.deactivation_reason,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO subscription_events
                        (email, previous_state, new_state, changed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (subscriber.email, previous_state, new_state, timestamp),
                )

    def get_daily_bars(self, instrument_id: str, *, adjustment: str = "raw") -> list[DailyBar]:
        """Read raw bars for one canonical instrument in session order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT instrument_id, session_date, source_timestamp, provider, feed,
                       frequency, open, high, low, close, volume, adjustment
                FROM daily_bars
                WHERE instrument_id = ? AND adjustment = ? ORDER BY session_date
                """,
                (instrument_id, adjustment),
            ).fetchall()
        return [
            DailyBar(
                instrument_id=row[0],
                session_date=date.fromisoformat(row[1]),
                source_timestamp=datetime.fromisoformat(row[2]),
                provider=row[3],
                feed=row[4],
                frequency=row[5],
                open=Decimal(row[6]),
                high=Decimal(row[7]),
                low=Decimal(row[8]),
                close=Decimal(row[9]),
                volume=row[10],
                adjustment=row[11],
            )
            for row in rows
        ]

    def list_instruments(self) -> list[Instrument]:
        """Return the current canonical catalog in deterministic symbol order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT instrument_id, canonical_symbol, provider_symbol, name, exchange,
                       instrument_type, status, tradable, first_observed, last_observed
                FROM instruments ORDER BY canonical_symbol
                """
            ).fetchall()
        return [
            Instrument(
                instrument_id=row[0],
                canonical_symbol=row[1],
                provider_symbol=row[2],
                name=row[3],
                exchange=Exchange(row[4]),
                instrument_type=InstrumentType(row[5]),
                status=row[6],
                tradable=bool(row[7]),
                first_observed=date.fromisoformat(row[8]),
                last_observed=date.fromisoformat(row[9]),
            )
            for row in rows
        ]

    def replace_universe_snapshot(self, session_date: date, instrument_ids: Iterable[str]) -> None:
        """Atomically replace the membership of one dated universe snapshot."""
        session = session_date.isoformat()
        members = sorted(set(instrument_ids))
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO universe_snapshots VALUES (?, ?)",
                (session, datetime.now().astimezone().isoformat()),
            )
            connection.execute(
                "DELETE FROM universe_snapshot_members WHERE session_date = ?", (session,)
            )
            connection.executemany(
                "INSERT INTO universe_snapshot_members VALUES (?, ?)",
                [(session, instrument_id) for instrument_id in members],
            )

    def get_universe_snapshot(self, session_date: date) -> list[str]:
        """Return canonical IDs eligible for a historical session."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT instrument_id FROM universe_snapshot_members
                WHERE session_date = ? ORDER BY instrument_id
                """,
                (session_date.isoformat(),),
            ).fetchall()
        return [row[0] for row in rows]

    def universe_snapshots(self, *, start: date, end: date) -> dict[date, frozenset[str]]:
        """Return point-in-time canonical symbols for a backtest interval."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT members.session_date, instruments.canonical_symbol
                FROM universe_snapshot_members AS members
                JOIN instruments USING (instrument_id)
                WHERE members.session_date BETWEEN ? AND ?
                ORDER BY members.session_date, instruments.canonical_symbol
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        grouped: dict[date, set[str]] = {}
        for raw_date, symbol in rows:
            grouped.setdefault(date.fromisoformat(raw_date), set()).add(str(symbol))
        return {session: frozenset(symbols) for session, symbols in grouped.items()}

    def cash_dividends(self, *, start: date, end: date) -> dict[tuple[str, date], Decimal]:
        """Return per-share cash distributions mapped to current canonical symbols."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT instruments.canonical_symbol, actions.effective_date,
                       actions.cash_amount
                FROM corporate_actions AS actions
                JOIN instruments ON instruments.provider_symbol = actions.provider_symbol
                WHERE actions.action_type = 'cash_dividend'
                  AND actions.effective_date BETWEEN ? AND ?
                  AND actions.cash_amount IS NOT NULL
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        result: dict[tuple[str, date], Decimal] = {}
        for symbol, raw_date, amount in rows:
            key = (str(symbol), date.fromisoformat(raw_date))
            result[key] = result.get(key, Decimal(0)) + Decimal(amount)
        return result

    def run_data_audit(self, progress: ProgressReporter = no_progress) -> int:
        """Quarantine incomplete adjusted series and inconsistent OHLC factors."""
        findings = 0
        with closing(self._connect()) as connection:
            progress(ProgressEvent("Scanning for incomplete adjustment series"))
            incomplete = connection.execute(
                """
                SELECT instrument_id, session_date, provider, feed, frequency,
                       GROUP_CONCAT(DISTINCT adjustment)
                FROM daily_bars
                GROUP BY instrument_id, session_date, provider, feed, frequency
                HAVING COUNT(DISTINCT adjustment) < 3
                """
            ).fetchall()
            progress(ProgressEvent("Checking split-factor consistency"))
            comparisons = connection.execute(
                """
                SELECT raw.instrument_id, raw.session_date,
                       raw.open, raw.high, raw.low, raw.close,
                       adjusted.open, adjusted.high, adjusted.low, adjusted.close
                FROM daily_bars AS raw
                JOIN daily_bars AS adjusted
                  ON adjusted.instrument_id = raw.instrument_id
                 AND adjusted.session_date = raw.session_date
                 AND adjusted.provider = raw.provider
                 AND adjusted.feed = raw.feed
                 AND adjusted.frequency = raw.frequency
                 AND adjusted.adjustment = 'split'
                WHERE raw.adjustment = 'raw'
                """
            ).fetchall()
        progress(ProgressEvent("Persisting data-quality findings"))
        for instrument_id, session, provider, feed, frequency, adjustments in incomplete:
            findings += 1
            self.quarantine(
                source=str(provider),
                record_type="adjustment_series",
                reason="daily bar is missing one or more required adjustment series",
                payload={
                    "instrument_id": str(instrument_id),
                    "session_date": str(session),
                    "feed": str(feed),
                    "frequency": str(frequency),
                    "present_adjustments": str(adjustments),
                },
            )
        for row in comparisons:
            raw_prices = [Decimal(str(value)) for value in row[2:6]]
            adjusted_prices = [Decimal(str(value)) for value in row[6:10]]
            ratios = [
                adjusted / raw
                for raw, adjusted in zip(raw_prices, adjusted_prices, strict=True)
                if raw
            ]
            if ratios and max(ratios) - min(ratios) > Decimal("0.000001"):
                findings += 1
                self.quarantine(
                    source="alpaca",
                    record_type="adjustment_factor",
                    reason="split-adjustment factor is inconsistent across OHLC",
                    payload={"instrument_id": str(row[0]), "session_date": str(row[1])},
                )
        return findings

    def latest_daily_session(self, *, adjustment: str = "split") -> date | None:
        """Return the newest stored session for one explicitly named price series."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(session_date) FROM daily_bars WHERE adjustment = ?",
                (adjustment,),
            ).fetchone()
        return date.fromisoformat(str(row[0])) if row and row[0] is not None else None

    def database_integrity(self) -> bool:
        """Run SQLite's complete integrity check for the canonical local store."""
        with closing(self._connect()) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        return rows == [("ok",)]

    def count(self, table: str) -> int:
        """Return a row count for an allowlisted canonical table (tests/health)."""
        allowed = {
            "instruments",
            "instrument_symbols",
            "universe_snapshots",
            "universe_snapshot_members",
            "daily_bars",
            "corporate_actions",
            "quarantined_records",
            "notification_deliveries",
            "notification_attempts",
            "ingestion_runs",
            "ingestion_pages",
            "strategy_runs",
            "strategy_selections",
            "sec_filings",
            "operation_locks",
            "operation_runs",
            "subscribers",
            "subscription_events",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        with closing(self._connect()) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
