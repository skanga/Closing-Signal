"""Typed, testable adapter for Alpaca's official REST APIs."""

import json
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast
from zoneinfo import ZoneInfo

import requests

from closing_signal.core.http import RetryPolicy, call_with_retry
from closing_signal.domain.models import (
    CorporateAction,
    DailyBar,
    Exchange,
    Instrument,
    InstrumentType,
)
from closing_signal.market.calendar import MarketSession


class HttpResponse(Protocol):
    """Small response surface used by the adapter."""

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpSession(Protocol):
    """Injectable HTTP boundary used to keep provider tests offline."""

    headers: MutableMapping[str, str]

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> HttpResponse: ...


class AssetClassifier(Protocol):
    """Resolve provider metadata to an allowed canonical security type."""

    def prepare(self, assets: Sequence[Mapping[str, object]]) -> None: ...

    def classify(self, asset: Mapping[str, object]) -> InstrumentType | None: ...

    def failure_reason(self, asset: Mapping[str, object]) -> str | None: ...

    def warnings(self, asset: Mapping[str, object]) -> tuple[str, ...]: ...


class ExplicitAssetClassifier:
    """Accept only an explicit security-type field supplied by a trusted source.

    Alpaca's core asset schema does not reliably distinguish common stocks, ETFs,
    and ADRs. This classifier deliberately refuses to infer a type from a symbol or
    company name. A production deployment must inject an authoritative enrichment
    implementation.
    """

    _MAPPING: ClassVar[dict[str, InstrumentType]] = {
        "common_stock": InstrumentType.COMMON_STOCK,
        "common stock": InstrumentType.COMMON_STOCK,
        "etf": InstrumentType.ETF,
        "adr": InstrumentType.ADR,
    }

    def prepare(self, assets: Sequence[Mapping[str, object]]) -> None:
        del assets

    def classify(self, asset: Mapping[str, object]) -> InstrumentType | None:
        value = asset.get("security_type")
        return self._MAPPING.get(str(value).strip().lower()) if value is not None else None

    def failure_reason(self, asset: Mapping[str, object]) -> str | None:
        return None if self.classify(asset) is not None else "security type is unresolved"

    def warnings(self, asset: Mapping[str, object]) -> tuple[str, ...]:
        del asset
        return ()


class JsonAssetClassifier:
    """Resolve security types from an explicitly selected reference-data export."""

    def __init__(self, classifications: Mapping[str, InstrumentType]) -> None:
        self.classifications = {
            symbol.strip().upper(): instrument_type
            for symbol, instrument_type in classifications.items()
        }

    def prepare(self, assets: Sequence[Mapping[str, object]]) -> None:
        del assets

    @classmethod
    def load(cls, path: str | Path) -> "JsonAssetClassifier":
        """Load a JSON object mapping provider symbols to canonical types."""
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("asset classification file must contain a JSON object")
        classifications: dict[str, InstrumentType] = {}
        for symbol, raw_type in parsed.items():
            if not isinstance(symbol, str) or not isinstance(raw_type, str):
                raise ValueError("asset classification entries must map strings to strings")
            classifications[symbol] = InstrumentType(raw_type)
        return cls(classifications)

    def classify(self, asset: Mapping[str, object]) -> InstrumentType | None:
        symbol = str(asset.get("symbol", "")).strip().upper()
        return self.classifications.get(symbol)

    def failure_reason(self, asset: Mapping[str, object]) -> str | None:
        return None if self.classify(asset) is not None else "security type is unresolved"

    def warnings(self, asset: Mapping[str, object]) -> tuple[str, ...]:
        del asset
        return ()


@dataclass(frozen=True, slots=True)
class RejectedInstrument:
    """A provider record excluded from the canonical universe with its reason."""

    provider_symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class InstrumentFetchResult:
    """Accepted and explicitly rejected results of one catalog fetch."""

    accepted: tuple[Instrument, ...]
    rejected: tuple[RejectedInstrument, ...]
    warnings: tuple[RejectedInstrument, ...] = ()


class AlpacaClient:
    """Alpaca catalog and historical daily-bar client."""

    _TRADING_BASE_URL = "https://paper-api.alpaca.markets"
    _DATA_BASE_URL = "https://data.alpaca.markets"
    _ACTION_GROUP_TYPES: ClassVar[dict[str, str]] = {
        "forward_splits": "forward_split",
        "reverse_splits": "reverse_split",
        "unit_splits": "unit_split",
        "cash_dividends": "cash_dividend",
        "stock_dividends": "stock_dividend",
        "spin_offs": "spin_off",
        "cash_mergers": "cash_merger",
        "stock_mergers": "stock_merger",
        "stock_and_cash_mergers": "stock_and_cash_merger",
        "redemptions": "redemption",
        "name_changes": "name_change",
        "worthless_removals": "worthless_removal",
        "rights_distributions": "rights_distribution",
        "partial_calls": "partial_call",
        "reorganizations": "reorganization",
    }

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        feed: str,
        classifier: AssetClassifier,
        session: HttpSession | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        asset_base_url: str = _TRADING_BASE_URL,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Alpaca credentials are required")
        if not feed:
            raise ValueError("Alpaca feed must be explicitly configured")
        self._session = session or cast(HttpSession, requests.Session())
        self._session.headers.update(
            {
                "APCA-API-KEY-ID": api_key,
                "APCA-API-SECRET-KEY": api_secret,
                "Accept": "application/json",
            }
        )
        self.feed = feed
        self.classifier = classifier
        self.timeout = timeout
        self.retry_policy = retry_policy
        self.asset_base_url = asset_base_url.rstrip("/")

    def fetch_instruments(self, *, observed_on: date) -> InstrumentFetchResult:
        """Fetch and strictly map Alpaca's U.S.-equity asset catalog."""
        response = self._get(
            f"{self.asset_base_url}/v2/assets",
            params={"asset_class": "us_equity", "status": "all"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Alpaca assets response must be a list")

        assets = [cast(dict[str, object], raw) for raw in payload if isinstance(raw, dict)]
        self.classifier.prepare(assets)
        accepted: list[Instrument] = []
        rejected: list[RejectedInstrument] = []
        warnings: list[RejectedInstrument] = []
        for raw in payload:
            if not isinstance(raw, dict):
                rejected.append(RejectedInstrument("<missing>", "record is not an object"))
                continue
            asset = cast(dict[str, object], raw)
            symbol = str(asset.get("symbol", "")).strip()
            try:
                exchange = Exchange(str(asset.get("exchange", "")).upper())
            except ValueError:
                rejected.append(RejectedInstrument(symbol, "venue is not NYSE or Nasdaq"))
                continue
            instrument_type = self.classifier.classify(asset)
            if instrument_type is None:
                rejected.append(
                    RejectedInstrument(
                        symbol,
                        self.classifier.failure_reason(asset) or "security type is unresolved",
                    )
                )
                continue
            provider_id = str(asset.get("id", "")).strip()
            name = str(asset.get("name", "")).strip()
            status = str(asset.get("status", "")).strip()
            try:
                accepted.append(
                    Instrument(
                        instrument_id=f"alpaca:{provider_id}",
                        canonical_symbol=symbol,
                        provider_symbol=symbol,
                        name=name,
                        exchange=exchange,
                        instrument_type=instrument_type,
                        status=status,
                        tradable=asset.get("tradable") is True,
                        first_observed=observed_on,
                        last_observed=observed_on,
                    )
                )
                warnings.extend(
                    RejectedInstrument(symbol, warning)
                    for warning in self.classifier.warnings(asset)
                )
            except ValueError as exc:
                rejected.append(RejectedInstrument(symbol, f"invalid metadata: {exc}"))
        return InstrumentFetchResult(tuple(accepted), tuple(rejected), tuple(warnings))

    def fetch_daily_bars(
        self, *, symbols: list[str], start: date, end: date, adjustment: str = "raw"
    ) -> Iterable[DailyBar]:
        """Yield all pages of raw daily bars for the requested inclusive range."""
        if not symbols:
            return
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "timeframe": "1Day",
            "start": start.isoformat(),
            "end": f"{end.isoformat()}T23:59:59Z",
            "adjustment": adjustment,
            "feed": self.feed,
            "limit": 10_000,
        }
        while True:
            response = self._get(
                f"{self._DATA_BASE_URL}/v2/stocks/bars",
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Alpaca bars response must be an object")
            page = cast(dict[str, object], payload)
            raw_bars = page.get("bars", {})
            if not isinstance(raw_bars, dict):
                raise ValueError("Alpaca bars field must be an object")
            for symbol, values in raw_bars.items():
                if not isinstance(values, list):
                    continue
                for raw in values:
                    if not isinstance(raw, dict):
                        continue
                    bar = cast(dict[str, object], raw)
                    source_timestamp = _parse_timestamp(str(bar["t"]))
                    yield DailyBar(
                        instrument_id=str(symbol),
                        session_date=source_timestamp.date(),
                        source_timestamp=source_timestamp,
                        provider="alpaca",
                        feed=self.feed,
                        frequency="1Day",
                        open=Decimal(str(bar["o"])),
                        high=Decimal(str(bar["h"])),
                        low=Decimal(str(bar["l"])),
                        close=Decimal(str(bar["c"])),
                        volume=int(cast(int | float | str, bar["v"])),
                        adjustment=adjustment,
                    )
            token = page.get("next_page_token")
            if not token:
                break
            params["page_token"] = str(token)

    def fetch_calendar(self, *, start: date, end: date) -> list[MarketSession]:
        """Fetch official market sessions, retaining session-specific close times."""
        response = self._get(
            f"{self.asset_base_url}/v2/calendar",
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Alpaca calendar response must be a list")
        timezone = ZoneInfo("America/New_York")
        sessions: list[MarketSession] = []
        for raw in payload:
            if not isinstance(raw, dict):
                raise ValueError("Alpaca calendar record must be an object")
            item = cast(dict[str, object], raw)
            session_date = date.fromisoformat(str(item["date"]))
            open_at = datetime.combine(
                session_date, time.fromisoformat(str(item["open"])), timezone
            )
            close_at = datetime.combine(
                session_date, time.fromisoformat(str(item["close"])), timezone
            )
            sessions.append(MarketSession(session_date, open_at, close_at))
        return sessions

    def fetch_corporate_actions(
        self, *, start: date, end: date, symbols: list[str] | None = None
    ) -> Iterable[CorporateAction]:
        """Yield every page and action type from Alpaca's v1 corporate-actions API."""
        params: dict[str, Any] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "region": "us",
            "limit": 1000,
            "sort": "asc",
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        while True:
            response = self._get(
                f"{self._DATA_BASE_URL}/v1/corporate-actions",
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Alpaca corporate-actions response must be an object")
            page = cast(dict[str, object], payload)
            grouped = page.get("corporate_actions")
            if not isinstance(grouped, dict):
                raise ValueError("Alpaca corporate_actions field must be an object")
            for group, values in grouped.items():
                action_type = self._ACTION_GROUP_TYPES.get(str(group))
                if action_type is None or not isinstance(values, list):
                    continue
                for raw in values:
                    if not isinstance(raw, dict):
                        continue
                    item = cast(dict[str, object], raw)
                    yield _corporate_action(item, action_type)
            token = page.get("next_page_token")
            if not token:
                break
            params["page_token"] = str(token)

    def _get(self, url: str, *, params: Mapping[str, Any]) -> HttpResponse:
        def request() -> HttpResponse:
            response = self._session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response

        return call_with_retry(request, self.retry_policy)


def _parse_timestamp(value: str) -> datetime:
    """Parse Alpaca's ISO-8601 timestamps, including the common ``Z`` suffix."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Alpaca timestamp is missing timezone information")
    return parsed


def _corporate_action(item: dict[str, object], action_type: str) -> CorporateAction:
    effective = item.get("effective_date") or item.get("ex_date")
    process = item.get("process_date")
    ratio = item.get("rate") or item.get("ratio")
    cash = item.get("cash_amount") or item.get("amount")
    return CorporateAction(
        provider_action_id=str(item.get("id", "")).strip(),
        provider="alpaca",
        action_type=action_type,
        provider_symbol=str(item.get("symbol", "")).strip(),
        effective_date=date.fromisoformat(str(effective)) if effective else None,
        process_date=date.fromisoformat(str(process)) if process else None,
        ratio=Decimal(str(ratio)) if ratio is not None else None,
        cash_amount=Decimal(str(cash)) if cash is not None else None,
        new_symbol=str(item["new_symbol"]).strip() if item.get("new_symbol") else None,
        source_payload=item,
    )
