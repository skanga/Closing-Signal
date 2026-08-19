"""SEC EDGAR adapter with fair-access throttling and explainable classification."""

import re
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from fnmatch import fnmatchcase
from html.parser import HTMLParser
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import requests

from closing_signal.core.http import RetryPolicy, call_with_retry

SEC_RATE_LIMIT_PER_SECOND = 10


class HttpResponse(Protocol):
    text: str

    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class HttpSession(Protocol):
    headers: dict[str, str]

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> HttpResponse: ...


class RequestRateLimiter:
    """Thread-safe fixed-spacing limiter capped at the SEC fair-access ceiling."""

    def __init__(self, requests_per_second: int = SEC_RATE_LIMIT_PER_SECOND) -> None:
        if not 1 <= requests_per_second <= SEC_RATE_LIMIT_PER_SECOND:
            raise ValueError("SEC requests_per_second must be between 1 and 10")
        self._interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Reserve the next request slot across all threads using this limiter."""
        with self._lock:
            now = time.monotonic()
            remaining = self._interval - (now - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
            self._last_request = time.monotonic()


_SEC_LIMITER = RequestRateLimiter()


@dataclass(frozen=True, slots=True)
class SECFiling:
    """Canonical filing discovery record keyed by accession number."""

    accession_number: str
    cik: int
    issuer: str
    symbol: str
    form: str
    filing_date: date
    accepted_at: datetime
    primary_document: str
    source_url: str


@dataclass(frozen=True, slots=True)
class FilingClassification:
    """Non-suppressing filing classification with evidence and uncertainty."""

    classification: str
    confidence: str
    uncertain: bool
    matched_evidence: tuple[str, ...]
    reason: str
    is_potentially_relevant: bool = True


class FilingClassifier:
    """Classify candidate documents using versionable, operator-supplied rules."""

    def __init__(self, *, rules: Mapping[str, Sequence[str]]) -> None:
        if not rules or any(not category or not evidence for category, evidence in rules.items()):
            raise ValueError("at least one non-empty SEC classification rule is required")
        self.rules = {category: tuple(patterns) for category, patterns in rules.items()}

    def classify(self, text: str) -> FilingClassification:
        """Return an alert even when no configured evidence can classify it."""
        normalized = text.casefold()
        matches: list[tuple[str, tuple[str, ...]]] = []
        for category, patterns in self.rules.items():
            evidence = tuple(pattern for pattern in patterns if pattern.casefold() in normalized)
            if evidence:
                matches.append((category, evidence))
        if not matches:
            return FilingClassification(
                classification="uncertain",
                confidence="low",
                uncertain=True,
                matched_evidence=(),
                reason="candidate form matched, but no configured offering evidence matched",
            )
        matches.sort(key=lambda item: (-len(item[1]), item[0]))
        category, evidence = matches[0]
        return FilingClassification(
            classification=category,
            confidence="high" if len(evidence) > 1 else "medium",
            uncertain=False,
            matched_evidence=evidence,
            reason=f"matched configured {category} evidence",
        )


class EdgarClient:
    """Read issuer submissions from the official data.sec.gov API."""

    _SUBMISSIONS_URL = "https://data.sec.gov/submissions"
    _ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

    def __init__(
        self,
        *,
        organization: str,
        contact_email: str,
        candidate_forms: frozenset[str],
        session: HttpSession | None = None,
        limiter: RequestRateLimiter = _SEC_LIMITER,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        if not organization.strip() or "@" not in contact_email:
            raise ValueError("SEC organization and contact email are required")
        if not candidate_forms:
            raise ValueError("candidate_forms must be explicitly configured")
        self._session = session or cast(HttpSession, requests.Session())
        self._session.headers.update(
            {
                "User-Agent": f"{organization.strip()} {contact_email.strip().lower()}",
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.candidate_forms = frozenset(form.upper() for form in candidate_forms)
        self.limiter = limiter
        self.timeout = timeout
        self.retry_policy = retry_policy

    def discover_filings(
        self, *, cik: int, symbol: str, filing_date_from: date | None = None
    ) -> list[SECFiling]:
        """Return matching recent and explicitly bounded historical filings."""
        if cik <= 0:
            raise ValueError("CIK must be positive")
        response = self._get(f"{self._SUBMISSIONS_URL}/CIK{cik:010d}.json")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SEC submissions response must be an object")
        root = cast(dict[str, object], payload)
        issuer = str(root.get("name", "")).strip()
        filing_sets = [_recent_filings(root)]
        if filing_date_from is not None:
            for filename in _historical_filenames(root, filing_date_from):
                historical_payload = self._get(f"{self._SUBMISSIONS_URL}/{filename}").json()
                if not isinstance(historical_payload, dict):
                    raise ValueError("SEC historical submissions response must be an object")
                filing_sets.append(_filing_arrays(cast(dict[str, object], historical_payload)))
        filings: list[SECFiling] = []
        seen_accessions: set[str] = set()
        for filing_set in filing_sets:
            for index, form_value in enumerate(filing_set["form"]):
                form = str(form_value).upper()
                filing_date = date.fromisoformat(str(filing_set["filingDate"][index]))
                if filing_date_from is not None and filing_date < filing_date_from:
                    continue
                if not any(fnmatchcase(form, pattern) for pattern in self.candidate_forms):
                    continue
                accession = str(filing_set["accessionNumber"][index])
                if accession in seen_accessions:
                    continue
                seen_accessions.add(accession)
                primary_document = str(filing_set["primaryDocument"][index])
                compact_accession = accession.replace("-", "")
                source_url = f"{self._ARCHIVES_URL}/{cik}/{compact_accession}/{primary_document}"
                filings.append(
                    SECFiling(
                        accession_number=accession,
                        cik=cik,
                        issuer=issuer,
                        symbol=symbol,
                        form=form,
                        filing_date=filing_date,
                        accepted_at=_parse_sec_timestamp(
                            str(filing_set["acceptanceDateTime"][index])
                        ),
                        primary_document=primary_document,
                        source_url=source_url,
                    )
                )
        return filings

    def fetch_company_tickers(self) -> dict[str, int]:
        """Map current ticker symbols to CIKs from the official SEC export."""
        response = self._get("https://www.sec.gov/files/company_tickers.json")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SEC company tickers response must be an object")
        mapping: dict[str, int] = {}
        for raw in payload.values():
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("ticker", "")).strip().upper()
            cik = raw.get("cik_str")
            if symbol and isinstance(cik, int) and cik > 0:
                mapping[symbol] = cik
        return mapping

    def fetch_document_text(self, filing: SECFiling) -> str:
        """Retrieve a primary filing document and strip markup for rule matching."""
        response = self._get(filing.source_url)
        parser = _VisibleTextParser()
        parser.feed(response.text)
        return parser.text

    def _get(self, url: str) -> HttpResponse:
        def request() -> HttpResponse:
            self.limiter.wait()
            response = self._session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response

        return call_with_retry(request, self.retry_policy)


class _VisibleTextParser(HTMLParser):
    """Extract visible-ish text while excluding executable/style content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.casefold() in {"script", "style"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() in {"script", "style"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data.strip():
            self._parts.append(data.strip())

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def _recent_filings(root: Mapping[str, object]) -> dict[str, list[object]]:
    filings = root.get("filings")
    if not isinstance(filings, dict) or not isinstance(filings.get("recent"), dict):
        raise ValueError("SEC submissions response has no recent filings")
    return _filing_arrays(cast(dict[str, object], filings["recent"]))


def _filing_arrays(payload: Mapping[str, object]) -> dict[str, list[object]]:
    """Validate the SEC's columnar recent or historical filing representation."""
    required = ("accessionNumber", "filingDate", "acceptanceDateTime", "form", "primaryDocument")
    result: dict[str, list[object]] = {}
    for key in required:
        value = payload.get(key)
        if not isinstance(value, list):
            raise ValueError(f"SEC filings field {key} must be a list")
        result[key] = cast(list[object], value)
    lengths = {len(values) for values in result.values()}
    if len(lengths) != 1:
        raise ValueError("SEC filing arrays have inconsistent lengths")
    return result


def _historical_filenames(root: Mapping[str, object], boundary: date) -> tuple[str, ...]:
    filings = root.get("filings")
    if not isinstance(filings, dict):
        raise ValueError("SEC submissions response has no filings object")
    raw_files = filings.get("files", [])
    if not isinstance(raw_files, list):
        raise ValueError("SEC historical files field must be a list")
    filenames: list[str] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise ValueError("SEC historical file entry must be an object")
        filename = str(raw_file.get("name", ""))
        filing_to = date.fromisoformat(str(raw_file.get("filingTo", "")))
        if filing_to < boundary:
            continue
        if re.fullmatch(r"[A-Za-z0-9._-]+\.json", filename) is None:
            raise ValueError("SEC historical filename is invalid")
        filenames.append(filename)
    return tuple(filenames)


def _parse_sec_timestamp(value: str) -> datetime:
    if value.isdigit() and len(value) == 14:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("America/New_York"))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/New_York"))
    return parsed
