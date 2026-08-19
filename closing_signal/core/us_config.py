"""Validated U.S.-product configuration with strict secret separation."""

import json
import re
import tomllib
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PositiveInt, SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ConfigurationFileError(ValueError):
    """Raised when a non-secret configuration file is unsafe or malformed."""


_SECRET_FILE_KEYS = {
    "alpaca_api_key",
    "alpaca_api_secret",
    "openfigi_api_key",
    "sec_organization",
    "sec_contact_email",
    "smtp_username",
    "smtp_password",
}
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AppSettings(BaseSettings):
    """All choices required to start U.S. synchronization or notification.

    Fields whose production value remains unresolved in the PRD have no default.
    This makes ``validate-config`` the explicit gate rather than hiding a product
    decision in application code.
    """

    storage_backend: Literal["sqlite"]
    database_path: Path
    asset_classification_source: Literal["openfigi", "json"]
    asset_classification_file: Path | None = None
    reference_reconciliation_sources: tuple[Literal["nasdaq", "sec"], ...]
    ingestion_chunk_size: PositiveInt
    http_max_attempts: PositiveInt
    http_base_delay_seconds: float = Field(ge=0)
    http_max_delay_seconds: float = Field(ge=0)
    http_jitter_seconds: float = Field(ge=0)
    alpaca_feed: Literal["sip", "iex"]
    alpaca_asset_base_url: Literal["https://paper-api.alpaca.markets", "https://api.alpaca.markets"]
    finalization_delay_minutes: PositiveInt
    historical_refetch_sessions: PositiveInt
    corporate_action_refetch_sessions: PositiveInt
    strategy_config_version: str
    strategy_parameters_file: Path
    backtest_config_version: str
    sec_candidate_forms: tuple[str, ...]
    sec_history_start: date
    sec_classification_rules_file: Path
    email_transport: Literal["smtp"]
    email_template_version: str
    email_max_attempts: PositiveInt
    email_backoff_seconds: tuple[float, ...]
    email_daily_send_limit: PositiveInt
    security_link_template: str
    smtp_host: str | None = None
    smtp_port: PositiveInt | None = None
    smtp_from_address: str | None = None
    smtp_security: Literal["ssl", "starttls", "none"] | None = None
    subscriber_file: Path
    health_market_max_age_sessions: int = Field(ge=0)
    health_required_operations: tuple[Literal["sync-daily", "screen", "sec-sync"], ...]
    health_operation_max_age_hours: PositiveInt
    health_min_free_disk_bytes: int = Field(ge=0)

    alpaca_api_key: SecretStr
    alpaca_api_secret: SecretStr
    openfigi_api_key: SecretStr | None = None
    sec_organization: str
    sec_contact_email: str
    smtp_username: SecretStr | None = None
    smtp_password: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_prefix="CLOSING_SIGNAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Make real environment variables override non-secret file values."""
        return env_settings, init_settings, dotenv_settings, file_secret_settings

    @model_validator(mode="after")
    def validate_operational_dependencies(self) -> "AppSettings":
        """Validate dependent transport fields and public contact addresses."""
        missing: list[str] = []
        if self.asset_classification_source == "openfigi":
            if self.openfigi_api_key is None:
                missing.append("openfigi_api_key")
            if set(self.reference_reconciliation_sources) != {"nasdaq", "sec"}:
                raise ValueError("OpenFIGI classification requires Nasdaq and SEC reconciliation")
        elif self.asset_classification_file is None:
            missing.append("asset_classification_file")
        if self.email_transport == "smtp":
            if not self.smtp_host:
                missing.append("smtp_host")
            if self.smtp_port is None:
                missing.append("smtp_port")
            if not self.smtp_from_address:
                missing.append("smtp_from_address")
            elif not _EMAIL_PATTERN.fullmatch(self.smtp_from_address):
                raise ValueError("smtp_from_address is not a valid email address")
            if self.smtp_security is None:
                missing.append("smtp_security")
        if not _EMAIL_PATTERN.fullmatch(self.sec_contact_email):
            raise ValueError("sec_contact_email is not a valid email address")
        if missing:
            raise ValueError(f"smtp transport requires: {', '.join(missing)}")
        if any(delay < 0 for delay in self.email_backoff_seconds):
            raise ValueError("email_backoff_seconds cannot contain negative values")
        if self.http_max_delay_seconds < self.http_base_delay_seconds:
            raise ValueError("http_max_delay_seconds cannot be less than the base delay")
        if not self.sec_candidate_forms or any(
            not form.strip() for form in self.sec_candidate_forms
        ):
            raise ValueError("sec_candidate_forms must contain at least one form")
        required_health_operations = {"sync-daily", "screen", "sec-sync"}
        if set(self.health_required_operations) != required_health_operations:
            raise ValueError(
                "health_required_operations must contain sync-daily, screen, and sec-sync"
            )
        if len(set(self.health_required_operations)) != len(self.health_required_operations):
            raise ValueError("health_required_operations cannot contain duplicates")
        if (
            not self.security_link_template.startswith("https://")
            or "{symbol}" not in self.security_link_template
        ):
            raise ValueError("security_link_template must be HTTPS and contain {symbol}")
        return self


def load_settings(path: str | Path) -> AppSettings:
    """Load JSON or TOML non-secret configuration, then apply env overrides."""
    config_path = Path(path)
    try:
        raw = config_path.read_bytes()
    except OSError as exc:
        raise ConfigurationFileError(f"cannot read configuration file: {config_path}") from exc

    try:
        if config_path.suffix.lower() == ".json":
            parsed = json.loads(raw.decode("utf-8"))
        elif config_path.suffix.lower() == ".toml":
            parsed = tomllib.loads(raw.decode("utf-8"))
        else:
            raise ConfigurationFileError("configuration file must use .json or .toml")
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationFileError(f"invalid configuration file: {config_path}") from exc

    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ConfigurationFileError("configuration file must contain a top-level object")
    data = dict[str, Any](parsed)
    forbidden = sorted(_SECRET_FILE_KEYS.intersection(data))
    if forbidden:
        raise ConfigurationFileError(
            f"secret keys are forbidden in configuration files: {', '.join(forbidden)}"
        )
    return AppSettings(**data)
