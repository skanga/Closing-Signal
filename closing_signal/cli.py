"""Non-interactive operator command surface for Closing Signal."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from closing_signal.core.progress import ProgressEvent, ProgressReporter, StderrProgressReporter
from closing_signal.core.us_config import AppSettings, ConfigurationFileError, load_settings
from closing_signal.data.repository import SQLiteRepository
from closing_signal.operations import (
    backfill,
    data_audit,
    health_check,
    retry_notifications,
    run_backtest,
    screen,
    sec_sync,
    sync_daily,
    sync_universe,
    validate_operational_files,
)

COMMANDS = {
    "validate-config",
    "sync-universe",
    "sync-daily",
    "backfill",
    "screen",
    "sec-sync",
    "retry-notifications",
    "backtest",
    "health",
    "data-audit",
}


def build_parser() -> argparse.ArgumentParser:
    """Build the complete P0 command surface with explicit config selection."""
    parser = argparse.ArgumentParser(description="Closing Signal U.S. equities operator")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to a non-secret JSON or TOML configuration file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config", help="Validate all configuration inputs")

    universe = subparsers.add_parser("sync-universe", help="Synchronize the Alpaca catalog")
    universe.add_argument("--as-of", type=_iso_date)

    daily = subparsers.add_parser("sync-daily", help="Synchronize completed EOD bars")
    daily.add_argument("--session", type=_iso_date)

    backfill = subparsers.add_parser("backfill", help="Backfill EOD history")
    backfill.add_argument("--start", type=_iso_date)
    backfill.add_argument("--end", type=_iso_date)

    screen = subparsers.add_parser("screen", help="Run enabled strategies")
    screen.add_argument("--session", type=_iso_date)
    screen.add_argument("--dry-run", action="store_true")
    screen.add_argument("--reprocess", action="store_true")

    sec_sync = subparsers.add_parser("sec-sync", help="Discover and classify SEC filings")
    sec_sync.add_argument("--dry-run", action="store_true")

    retry = subparsers.add_parser(
        "retry-notifications", help="Retry failed recipients without resending successes"
    )
    retry.add_argument("--dry-run", action="store_true")
    retry.add_argument("--session", type=_iso_date)

    backtest = subparsers.add_parser("backtest", help="Run a configured historical evaluation")
    backtest.add_argument("--request", type=Path, required=True)

    subparsers.add_parser("health", help="Check configuration and local state")
    subparsers.add_parser("data-audit", help="Report data-quality and quarantine findings")
    return parser


def run(argv: Sequence[str] | None = None) -> int:
    """Execute one command and return zero only for its defined success state."""
    args = build_parser().parse_args(argv)
    progress = StderrProgressReporter(args.command)
    progress(ProgressEvent("Starting"))
    try:
        settings = load_settings(args.config)
    except (ConfigurationFileError, ValidationError) as exc:
        _write_failure("configuration invalid", diagnostic=f"configuration invalid: {exc}")
        return 2

    if args.command == "validate-config":
        try:
            validate_operational_files(settings)
        except (OSError, ValueError) as exc:
            _write_failure("configuration invalid", diagnostic=f"configuration invalid: {exc}")
            return 2
        _write_result({"status": "complete", "message": "configuration valid"})
        return 0
    repository = SQLiteRepository(settings.database_path)
    if args.command == "health":
        status, result = _capture_result(lambda: health_check(args, settings, repository))
        _write_result(result)
        return status
    handler = _OPERATION_HANDLERS.get(args.command)
    if handler is None:
        message = f"command not implemented: {args.command}"
        _write_failure(message)
        return 3
    owner = str(uuid.uuid4())
    lock_name = "mutating-operation"
    if not repository.acquire_operation_lock(lock_name, owner):
        message = f"operation already running: {args.command}"
        _write_failure(message)
        return 5
    failure: Exception | None = None
    result: dict[str, object] | None = None
    status = 4
    try:
        repository.start_operation_run(owner, args.command)
        try:
            status, result = _capture_result(
                lambda: handler(args, settings, repository, progress)
            )
        except Exception as exc:
            repository.finish_operation_run(
                owner,
                status="failed",
                exit_code=4,
                error_type=type(exc).__name__,
            )
            raise
        repository.finish_operation_run(
            owner,
            status="complete" if status == 0 else "failed",
            exit_code=status,
            error_type=None,
        )
    except Exception as exc:
        failure = exc
    repository.release_operation_lock(lock_name, owner)
    if failure is not None:
        message = f"{args.command} failed: {type(failure).__name__}"
        _write_failure(message)
        return 4
    if result is None:  # pragma: no cover - guarded by the captured-result contract.
        raise RuntimeError("command completed without a result")
    _write_result(result)
    return status


_OPERATION_HANDLERS: dict[
    str, Callable[[argparse.Namespace, AppSettings, SQLiteRepository, ProgressReporter], int]
] = {
    "sync-universe": sync_universe,
    "sync-daily": sync_daily,
    "backfill": backfill,
    "screen": screen,
    "sec-sync": sec_sync,
    "retry-notifications": retry_notifications,
    "backtest": run_backtest,
    "data-audit": data_audit,
}


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _capture_result(operation: Callable[[], int]) -> tuple[int, dict[str, object]]:
    """Buffer and validate one handler result before exposing it to automation."""
    output = StringIO()
    with redirect_stdout(output):
        status = operation()
    try:
        result: object = json.loads(output.getvalue())
    except json.JSONDecodeError as exc:
        raise ValueError("command result must be one JSON document") from exc
    if not isinstance(result, dict):
        raise ValueError("command result must be a JSON object")
    return status, cast(dict[str, object], result)


def _write_result(result: dict[str, object]) -> None:
    """Emit one canonical machine-readable terminal result."""
    print(json.dumps(result, sort_keys=True))


def _write_failure(error: str, *, diagnostic: str | None = None) -> None:
    """Keep stable automation output separate from best-effort human diagnostics."""
    _write_diagnostic(diagnostic or error)
    _write_result({"status": "failed", "error": error})


def _write_diagnostic(message: str) -> None:
    stream = sys.stderr
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except Exception:
        return


def main() -> None:
    """Installed entry point."""
    try:
        status = run()
    except Exception as exc:
        _write_failure(
            "operator command failed unexpectedly",
            diagnostic=f"operator command failed unexpectedly: {type(exc).__name__}",
        )
        raise SystemExit(1) from None
    if status:
        raise SystemExit(status)


if __name__ == "__main__":
    main()
