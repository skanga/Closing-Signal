"""Configuration contracts for explicit choices and secret separation."""

import json

import pytest
from pydantic import ValidationError

from closing_signal.core.us_config import AppSettings, ConfigurationFileError, load_settings


def _non_secret_config() -> dict[str, object]:
    return {
        "storage_backend": "sqlite",
        "database_path": "data/us-equities.db",
        "asset_classification_source": "openfigi",
        "reference_reconciliation_sources": ["nasdaq", "sec"],
        "ingestion_chunk_size": 100,
        "http_max_attempts": 3,
        "http_base_delay_seconds": 1,
        "http_max_delay_seconds": 8,
        "http_jitter_seconds": 0.25,
        "alpaca_feed": "sip",
        "alpaca_asset_base_url": "https://paper-api.alpaca.markets",
        "finalization_delay_minutes": 45,
        "historical_refetch_sessions": 5,
        "corporate_action_refetch_sessions": 30,
        "strategy_config_version": "strategies-v1",
        "strategy_parameters_file": "config/strategies.json",
        "backtest_config_version": "backtests-v1",
        "sec_candidate_forms": ["S-1", "S-3", "424B5", "8-K", "D"],
        "sec_history_start": "2016-01-01",
        "sec_classification_rules_file": "config/sec-rules.json",
        "email_transport": "smtp",
        "email_template_version": "email-v1",
        "email_max_attempts": 3,
        "email_backoff_seconds": [1, 5],
        "email_daily_send_limit": 300,
        "security_link_template": "https://example.test/security/{symbol}",
        "smtp_host": "smtp.example.test",
        "smtp_port": 587,
        "smtp_from_address": "alerts@example.test",
        "smtp_security": "starttls",
        "subscriber_file": "config/subscribers.json",
        "health_market_max_age_sessions": 1,
        "health_required_operations": ["sync-daily", "screen", "sec-sync"],
        "health_operation_max_age_hours": 48,
        "health_min_free_disk_bytes": 1073741824,
    }


def _set_secrets(monkeypatch) -> None:
    monkeypatch.setenv("CLOSING_SIGNAL_ALPACA_API_KEY", "api-key")
    monkeypatch.setenv("CLOSING_SIGNAL_ALPACA_API_SECRET", "api-secret")
    monkeypatch.setenv("CLOSING_SIGNAL_OPENFIGI_API_KEY", "openfigi-key")
    monkeypatch.setenv("CLOSING_SIGNAL_SEC_ORGANIZATION", "Example Research LLC")
    monkeypatch.setenv("CLOSING_SIGNAL_SEC_CONTACT_EMAIL", "ops@example.test")
    monkeypatch.setenv("CLOSING_SIGNAL_SMTP_USERNAME", "mailer")
    monkeypatch.setenv("CLOSING_SIGNAL_SMTP_PASSWORD", "smtp-secret")


def test_file_configuration_loads_and_environment_wins(tmp_path, monkeypatch) -> None:
    config = _non_secret_config()
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    _set_secrets(monkeypatch)
    monkeypatch.setenv("CLOSING_SIGNAL_ALPACA_FEED", "iex")

    settings = load_settings(path)

    assert settings.alpaca_feed == "iex"
    assert settings.alpaca_api_key.get_secret_value() == "api-key"


def test_secret_keys_are_rejected_from_configuration_files(tmp_path, monkeypatch) -> None:
    config = _non_secret_config() | {"alpaca_api_secret": "must-not-be-here"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    _set_secrets(monkeypatch)

    with pytest.raises(ConfigurationFileError, match="secret keys"):
        load_settings(path)


def test_required_unresolved_choices_are_reported_together() -> None:
    with pytest.raises(ValidationError) as error:
        AppSettings(_env_file=None)

    message = str(error.value)
    assert "storage_backend" in message
    assert "alpaca_feed" in message
    assert "finalization_delay_minutes" in message
    assert "email_transport" in message


def test_smtp_transport_validates_dependent_fields(monkeypatch) -> None:
    _set_secrets(monkeypatch)
    config = _non_secret_config()
    config.pop("smtp_host")

    with pytest.raises(ValidationError, match="smtp_host"):
        AppSettings(**config)
