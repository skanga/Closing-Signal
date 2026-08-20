"""Operator command surface and exit-status contracts."""

import json
from types import SimpleNamespace

import pytest

from closing_signal import cli
from closing_signal.cli import COMMANDS, build_parser, run
from closing_signal.core.progress import ProgressEvent
from closing_signal.data.repository import SQLiteRepository


def _successful_handler(args, settings, repository, progress) -> int:
    del args, settings, repository, progress
    print(json.dumps({"status": "complete"}))
    return 0


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


def test_missing_configuration_returns_structured_failure(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.json"

    assert run(["--config", str(missing), "validate-config"]) == 2
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "configuration invalid",
    }
    assert "configuration invalid:" in captured.err


def test_validate_config_success_is_structured(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace())
    monkeypatch.setattr(cli, "validate_operational_files", lambda settings: None)

    assert run(["--config", "settings.json", "validate-config"]) == 0
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "complete",
        "message": "configuration valid",
    }
    assert captured.err == "[validate-config] Starting\n"


def test_validate_config_failure_is_structured(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace())

    def fail_validation(settings) -> None:
        del settings
        raise ValueError("fixture validation failure")

    monkeypatch.setattr(cli, "validate_operational_files", fail_validation)

    assert run(["--config", "settings.json", "validate-config"]) == 2
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "configuration invalid",
    }
    assert "configuration invalid: fixture validation failure" in captured.err


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
        _successful_handler,
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
        _successful_handler,
    )

    first_status = run(["--config", "settings.json", "sync-universe"])
    second_status = run(["--config", "settings.json", "sync-universe"])

    assert first_status == 4
    assert second_status == 0


def test_keyboard_interrupt_releases_global_lock(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    def interrupt(args, settings, repository, progress) -> int:
        del args, settings, repository, progress
        raise KeyboardInterrupt

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run(["--config", "settings.json", "sync-universe"])
    captured = capsys.readouterr()

    repository = SQLiteRepository(database)
    assert repository.acquire_operation_lock("mutating-operation", "next-owner") is True
    repository.release_operation_lock("mutating-operation", "next-owner")
    assert captured.out == ""


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


def test_lock_contention_writes_one_structured_failure(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    repository = SQLiteRepository(database)
    assert repository.acquire_operation_lock("mutating-operation", "existing-owner") is True
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    assert run(["--config", "settings.json", "sync-universe"]) == 5
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "operation already running: sync-universe",
    }
    assert "operation already running: sync-universe" in captured.err


def test_handler_failure_discards_captured_success(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    def handler(args, settings, repository, progress) -> int:
        del args, settings, repository, progress
        print(json.dumps({"status": "complete", "accepted": 3}))
        raise RuntimeError("fixture handler failure")

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", handler)

    assert run(["--config", "settings.json", "sync-universe"]) == 4
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "sync-universe failed: RuntimeError",
    }
    assert "sync-universe failed: RuntimeError" in captured.err


def test_finalization_failure_discards_captured_success(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    repository = SQLiteRepository(database)
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))
    monkeypatch.setattr(cli, "SQLiteRepository", lambda path: repository)

    def fail_finalization(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("fixture finalization failure")

    monkeypatch.setattr(repository, "finish_operation_run", fail_finalization)

    def handler(args, settings, selected_repository, progress) -> int:
        del args, settings, selected_repository, progress
        print(json.dumps({"status": "complete", "accepted": 3}))
        return 0

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", handler)

    assert run(["--config", "settings.json", "sync-universe"]) == 4
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "sync-universe failed: OSError",
    }
    assert "sync-universe failed: OSError" in captured.err


def test_handler_output_must_be_one_json_object(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    def handler(args, settings, repository, progress) -> int:
        del args, settings, repository, progress
        print('{"status": "complete"}')
        print('{"status": "complete"}')
        return 0

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", handler)

    assert run(["--config", "settings.json", "sync-universe"]) == 4
    captured = capsys.readouterr()

    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "sync-universe failed: ValueError",
    }


def test_top_level_fallback_writes_one_structured_failure(monkeypatch, capsys) -> None:
    def fail() -> int:
        raise RuntimeError("fixture top-level failure")

    monkeypatch.setattr(cli, "run", fail)

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    captured = capsys.readouterr()

    assert exc_info.value.code == 1
    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "operator command failed unexpectedly",
    }
    assert "operator command failed unexpectedly: RuntimeError" in captured.err


def test_lock_release_failure_preserves_top_level_exit_code(tmp_path, monkeypatch, capsys) -> None:
    database = tmp_path / "market.db"
    repository = SQLiteRepository(database)
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))
    monkeypatch.setattr(cli, "SQLiteRepository", lambda path: repository)
    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", _successful_handler)
    monkeypatch.setattr(
        repository,
        "release_operation_lock",
        lambda lock_name, owner: (_ for _ in ()).throw(OSError("fixture release failure")),
    )
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["closing-signal", "--config", "settings.json", "sync-universe"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()
    captured = capsys.readouterr()

    assert exc_info.value.code == 1
    assert json.loads(captured.out) == {
        "status": "failed",
        "error": "operator command failed unexpectedly",
    }
