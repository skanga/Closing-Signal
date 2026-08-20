"""Operator command surface and exit-status contracts."""

import json
from types import SimpleNamespace

from closing_signal import cli
from closing_signal.cli import COMMANDS, build_parser, run
from closing_signal.core.progress import ProgressEvent
from closing_signal.data.repository import SQLiteRepository


def test_parser_exposes_every_p0_operator_command() -> None:
    assert {
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
    } == COMMANDS
    parser = build_parser()
    for command in COMMANDS:
        values = ["--config", "settings.json", command]
        if command == "backtest":
            values.extend(["--request", "backtest.json"])
        arguments = parser.parse_args(values)
        assert arguments.command == command


def test_missing_configuration_returns_nonzero(tmp_path) -> None:
    missing = tmp_path / "missing.json"

    assert run(["--config", str(missing), "validate-config"]) != 0


def test_parser_supports_dry_run_and_explicit_reprocessing() -> None:
    parser = build_parser()

    screen = parser.parse_args(["--config", "settings.json", "screen", "--dry-run", "--reprocess"])

    assert screen.dry_run is True
    assert screen.reprocess is True


def test_config_path_is_required() -> None:
    parser = build_parser()

    try:
        parser.parse_args(["health"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("parser accepted a command without --config")


def test_dispatched_command_persists_completed_operation_state(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))
    monkeypatch.setitem(
        cli._OPERATION_HANDLERS,
        "sync-universe",
        lambda args, settings, repository, progress: 0,
    )

    status = run(["--config", "settings.json", "sync-universe"])
    latest = SQLiteRepository(database).latest_operation_run("sync-universe")

    assert status == 0
    assert latest is not None
    assert latest["status"] == "complete"


def test_operation_record_failure_releases_global_lock(tmp_path, monkeypatch) -> None:
    database = tmp_path / "market.db"
    repository = SQLiteRepository(database)
    real_start = repository.start_operation_run
    start_attempts = 0

    def fail_first_start(run_id: str, operation: str) -> None:
        nonlocal start_attempts
        start_attempts += 1
        if start_attempts == 1:
            raise OSError("simulated persistence failure")
        real_start(run_id, operation)

    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))
    monkeypatch.setattr(cli, "SQLiteRepository", lambda path: repository)
    monkeypatch.setattr(repository, "start_operation_run", fail_first_start)
    monkeypatch.setitem(
        cli._OPERATION_HANDLERS,
        "sync-universe",
        lambda args, settings, selected_repository, progress: 0,
    )

    first_status = run(["--config", "settings.json", "sync-universe"])
    second_status = run(["--config", "settings.json", "sync-universe"])

    assert first_status == 4
    assert second_status == 0


def test_run_writes_progress_to_stderr_and_result_to_stdout(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    def handler(args, settings, repository, progress) -> int:
        del args, settings, repository
        progress(ProgressEvent("Working", completed=1, total=2, unit="steps"))
        print(json.dumps({"status": "complete"}))
        return 0

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", handler)

    assert run(["--config", "settings.json", "sync-universe"]) == 0
    captured = capsys.readouterr()

    assert captured.out == '{"status": "complete"}\n'
    assert captured.err.splitlines() == [
        "[sync-universe] Starting",
        "[sync-universe] Working: 1/2 steps",
    ]
