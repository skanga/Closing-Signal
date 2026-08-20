"""OpenFIGI security typing reconciled against official listing associations."""

from __future__ import annotations

import csv
import io
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol, cast

import requests

from closing_signal.core.http import RetryPolicy, call_with_retry
from closing_signal.core.progress import ProgressEvent, ProgressReporter, no_progress, should_report
from closing_signal.domain.models import Exchange, InstrumentType


class HttpResponse(Protocol):
    """Response surface required by the reference-data clients."""

    @property
    def text(self) -> str: ...

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpSession(Protocol):
    """Injectable HTTP boundary for offline provider tests."""

    headers: MutableMapping[str, str]

    def get(self, url: str, *, timeout: float) -> HttpResponse: ...

    def post(self, url: str, *, json: object, timeout: float) -> HttpResponse: ...


@dataclass(frozen=True, slots=True)
class ReferenceClassificationResult:
    """Explicit primary classifications and symbol-specific failure evidence."""

    classifications: Mapping[str, InstrumentType]
    issues: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class NasdaqReference:
    """Nasdaq Trader listing-directory evidence."""

    exchange: Exchange
    is_etf: bool


@dataclass(frozen=True, slots=True)
class SECReference:
    """SEC company ticker/exchange association evidence."""

    exchange: Exchange


class PrimaryClassificationSource(Protocol):
    def fetch_classifications(
        self, assets: Sequence[Mapping[str, object]]
    ) -> ReferenceClassificationResult: ...


class NasdaqReferenceSource(Protocol):
    def fetch_references(self) -> Mapping[str, NasdaqReference]: ...


class SECReferenceSource(Protocol):
    def fetch_references(self) -> Mapping[str, SECReference]: ...


class OpenFigiClient:
    """Batch OpenFIGI mappings without inferring types from names or tickers."""

    _URL = "https://api.openfigi.com/v3/mapping"

    def __init__(
        self,
        *,
        api_key: str,
        session: HttpSession | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        request_interval: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        progress: ProgressReporter = no_progress,
    ) -> None:
        if not api_key:
            raise ValueError("OpenFIGI API key is required")
        if request_interval < 0:
            raise ValueError("OpenFIGI request interval cannot be negative")
        self._session = session or cast(HttpSession, requests.Session())
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-OPENFIGI-APIKEY": api_key,
            }
        )
        self.timeout = timeout
        self.retry_policy = retry_policy
        self.request_interval = request_interval
        self.sleep = sleep
        self.progress = progress

    def fetch_classifications(
        self, assets: Sequence[Mapping[str, object]]
    ) -> ReferenceClassificationResult:
        """Return one canonical type only when OpenFIGI evidence is unambiguous."""
        classifications: dict[str, InstrumentType] = {}
        issues: dict[str, str] = {}
        valid_assets: list[tuple[str, Exchange]] = []
        for asset in assets:
            symbol = _symbol(asset)
            try:
                exchange = Exchange(str(asset.get("exchange", "")).upper())
            except ValueError:
                continue
            if symbol:
                valid_assets.append((symbol, exchange))

        total_batches = (len(valid_assets) + 99) // 100
        for batch_index, offset in enumerate(range(0, len(valid_assets), 100)):
            batch = valid_assets[offset : offset + 100]
            batch_number = batch_index + 1
            if should_report(batch_number, total=total_batches, every=10):
                self.progress(
                    ProgressEvent(
                        "Classifying assets with OpenFIGI",
                        completed=batch_number,
                        total=total_batches,
                        unit="batches",
                    )
                )
            jobs = [
                {
                    "idType": "TICKER",
                    "idValue": symbol,
                    "exchCode": "US",
                    "marketSecDes": "Equity",
                }
                for symbol, _exchange in batch
            ]
            if batch_index:
                self.sleep(self.request_interval)
            response = call_with_retry(
                partial(self._session.post, self._URL, json=jobs, timeout=self.timeout),
                self.retry_policy,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) != len(batch):
                raise ValueError("OpenFIGI mapping response does not match the request batch")
            for (symbol, _), raw_result in zip(batch, payload, strict=True):
                mapped = _map_openfigi_result(raw_result)
                if isinstance(mapped, InstrumentType):
                    classifications[symbol] = mapped
                else:
                    issues[symbol] = mapped
        return ReferenceClassificationResult(classifications, issues)


class NasdaqDirectoryClient:
    """Download the current Nasdaq and other-listed symbol directories."""

    _NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
    _OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

    def __init__(
        self,
        *,
        session: HttpSession | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._session = session or cast(HttpSession, requests.Session())
        self.timeout = timeout
        self.retry_policy = retry_policy

    def fetch_references(self) -> Mapping[str, NasdaqReference]:
        nasdaq = self._get_text(self._NASDAQ_URL)
        other = self._get_text(self._OTHER_URL)
        return parse_nasdaq_directories(nasdaq, other)

    def _get_text(self, url: str) -> str:
        response = call_with_retry(
            lambda: self._session.get(url, timeout=self.timeout), self.retry_policy
        )
        response.raise_for_status()
        return response.text


class SECCompanyTickerClient:
    """Download SEC ticker/exchange associations for reconciliation only."""

    _URL = "https://www.sec.gov/files/company_tickers_exchange.json"

    def __init__(
        self,
        *,
        organization: str,
        contact_email: str,
        session: HttpSession | None = None,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not organization or not contact_email:
            raise ValueError("SEC identity is required")
        self._session = session or cast(HttpSession, requests.Session())
        self._session.headers.update(
            {"User-Agent": f"{organization} {contact_email}", "Accept": "application/json"}
        )
        self.timeout = timeout
        self.retry_policy = retry_policy

    def fetch_references(self) -> Mapping[str, SECReference]:
        response = call_with_retry(
            lambda: self._session.get(self._URL, timeout=self.timeout), self.retry_policy
        )
        response.raise_for_status()
        return parse_sec_company_tickers(response.json())


class ReconciledAssetClassifier:
    """Use OpenFIGI as primary and refuse explicit Nasdaq/SEC conflicts."""

    def __init__(
        self,
        primary: PrimaryClassificationSource,
        nasdaq: NasdaqReferenceSource,
        sec: SECReferenceSource,
        progress: ProgressReporter = no_progress,
    ) -> None:
        self.primary = primary
        self.nasdaq = nasdaq
        self.sec = sec
        self.progress = progress
        self._classifications: Mapping[str, InstrumentType] = {}
        self._failures: dict[str, str] = {}
        self._warnings: dict[str, tuple[str, ...]] = {}

    def prepare(self, assets: Sequence[Mapping[str, object]]) -> None:
        """Fetch each source once and reconcile the complete Alpaca catalog."""
        self.progress(ProgressEvent("Classifying the Alpaca catalog with OpenFIGI"))
        primary = self.primary.fetch_classifications(assets)
        self.progress(ProgressEvent("Reconciling the Nasdaq listing directories"))
        nasdaq = self.nasdaq.fetch_references()
        self.progress(ProgressEvent("Reconciling SEC company ticker associations"))
        sec = self.sec.fetch_references()
        self._classifications = primary.classifications
        self._failures = dict(primary.issues)
        self._warnings = {}
        for asset in assets:
            symbol = _symbol(asset)
            if not symbol or symbol in self._failures:
                continue
            instrument_type = self._classifications.get(symbol)
            if instrument_type is None:
                self._failures[symbol] = "OpenFIGI did not return an explicit supported type"
                continue
            try:
                exchange = Exchange(str(asset.get("exchange", "")).upper())
            except ValueError:
                continue
            directory = nasdaq.get(symbol)
            association = sec.get(symbol)
            if directory is not None and directory.exchange is not exchange:
                self._failures[symbol] = "Alpaca venue conflicts with Nasdaq directory"
            elif directory is not None and directory.is_etf != (
                instrument_type is InstrumentType.ETF
            ):
                self._failures[symbol] = "OpenFIGI type conflicts with Nasdaq ETF flag"
            elif association is not None and association.exchange is not exchange:
                self._failures[symbol] = "Alpaca venue conflicts with SEC association"
            else:
                warnings: list[str] = []
                if directory is None:
                    warnings.append("symbol missing from Nasdaq reconciliation source")
                if association is None and instrument_type is not InstrumentType.ETF:
                    warnings.append("symbol missing from SEC reconciliation source")
                if warnings:
                    self._warnings[symbol] = tuple(warnings)

    def classify(self, asset: Mapping[str, object]) -> InstrumentType | None:
        symbol = _symbol(asset)
        if symbol in self._failures:
            return None
        return self._classifications.get(symbol)

    def failure_reason(self, asset: Mapping[str, object]) -> str | None:
        return self._failures.get(_symbol(asset))

    def warnings(self, asset: Mapping[str, object]) -> tuple[str, ...]:
        return self._warnings.get(_symbol(asset), ())


def parse_nasdaq_directories(
    nasdaq_listed: str, other_listed: str
) -> Mapping[str, NasdaqReference]:
    """Parse only non-test Nasdaq and NYSE records with an explicit ETF flag."""
    references: dict[str, NasdaqReference] = {}
    for row in _pipe_rows(nasdaq_listed):
        symbol = str(row.get("Symbol", "")).strip().upper()
        if symbol and row.get("Test Issue") == "N" and row.get("ETF") in {"Y", "N"}:
            references[symbol] = NasdaqReference(Exchange.NASDAQ, row["ETF"] == "Y")
    for row in _pipe_rows(other_listed):
        symbol = str(row.get("ACT Symbol", "")).strip().upper()
        if (
            symbol
            and row.get("Exchange") == "N"
            and row.get("Test Issue") == "N"
            and row.get("ETF") in {"Y", "N"}
        ):
            references[symbol] = NasdaqReference(Exchange.NYSE, row["ETF"] == "Y")
    return references


def parse_sec_company_tickers(payload: object) -> Mapping[str, SECReference]:
    """Parse the SEC's field-described ticker/exchange table defensively."""
    if not isinstance(payload, dict):
        raise ValueError("SEC company ticker response must be an object")
    fields = payload.get("fields")
    data = payload.get("data")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise ValueError("SEC company ticker fields are invalid")
    if not isinstance(data, list):
        raise ValueError("SEC company ticker data is invalid")
    references: dict[str, SECReference] = {}
    for values in data:
        if not isinstance(values, list) or len(values) != len(fields):
            continue
        row = dict(zip(fields, values, strict=True))
        symbol = str(row.get("ticker", "")).strip().upper()
        exchange = _sec_exchange(str(row.get("exchange", "")))
        if symbol and exchange is not None:
            references[symbol] = SECReference(exchange)
    return references


def _pipe_rows(text: str) -> Sequence[Mapping[str, str]]:
    return tuple(csv.DictReader(io.StringIO(text), delimiter="|"))


def _sec_exchange(value: str) -> Exchange | None:
    normalized = value.strip().lower()
    if normalized == "nyse":
        return Exchange.NYSE
    if normalized == "nasdaq":
        return Exchange.NASDAQ
    return None


def _map_openfigi_result(raw_result: object) -> InstrumentType | str:
    if not isinstance(raw_result, dict):
        return "OpenFIGI result is not an object"
    data = raw_result.get("data")
    if isinstance(data, list) and data:
        types = {_openfigi_type(item) for item in data if isinstance(item, dict)}
        types.discard(None)
        if not types:
            return "OpenFIGI did not return an explicit supported type"
        if len(types) > 1:
            return "OpenFIGI returned conflicting security types"
        return cast(InstrumentType, types.pop())
    for key in ("error", "warning"):
        value = raw_result.get(key)
        if isinstance(value, str) and value.strip():
            message = " ".join(value.split())[:500]
            return f"OpenFIGI API {key}: {message}"
    return "OpenFIGI did not return a mapping"


def _openfigi_type(item: Mapping[str, Any]) -> InstrumentType | None:
    values = {
        str(item.get("securityType", "")).strip().lower(),
        str(item.get("securityType2", "")).strip().lower(),
    }
    if values & {"adr", "depositary receipt", "global depositary receipt"}:
        return InstrumentType.ADR
    if values & {"etf", "etp", "exchange traded fund", "exchange-traded fund"}:
        return InstrumentType.ETF
    if "common stock" in values:
        return InstrumentType.COMMON_STOCK
    return None


def _symbol(asset: Mapping[str, object]) -> str:
    return str(asset.get("symbol", "")).strip().upper()
