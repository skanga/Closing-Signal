"""Official EDGAR discovery and non-suppressing classification contracts."""

from collections.abc import Mapping
from datetime import date
from typing import Any

from closing_signal.sec.edgar import SEC_RATE_LIMIT_PER_SECOND, EdgarClient, FilingClassifier


class StubResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class StubSession:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> StubResponse:
        del timeout
        self.calls.append((url, params))
        return StubResponse(self.payload)


class RoutingSession:
    def __init__(self, payloads: Mapping[str, object]) -> None:
        self.payloads = payloads
        self.headers: dict[str, str] = {}
        self.urls: list[str] = []

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> StubResponse:
        del params, timeout
        self.urls.append(url)
        return StubResponse(self.payloads[url.rsplit("/", 1)[-1]])


def test_edgar_discovery_uses_identity_and_configured_candidate_forms() -> None:
    session = StubSession(
        {
            "name": "Example Corp",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002"],
                    "filingDate": ["2026-08-17", "2026-08-18"],
                    "acceptanceDateTime": ["20260817170000", "20260818170000"],
                    "form": ["10-Q", "S-3"],
                    "primaryDocument": ["q.htm", "s3.htm"],
                }
            },
        }
    )
    client = EdgarClient(
        organization="Example Research LLC",
        contact_email="ops@example.test",
        candidate_forms=frozenset({"S-3", "424B5"}),
        session=session,
    )

    filings = client.discover_filings(cik=1, symbol="EXM")

    assert len(filings) == 1
    assert filings[0].accession_number == "0000000001-26-000002"
    assert filings[0].source_url.endswith("/1/000000000126000002/s3.htm")
    assert session.headers["User-Agent"] == "Example Research LLC ops@example.test"
    assert SEC_RATE_LIMIT_PER_SECOND == 10


def test_candidate_without_keyword_evidence_is_still_an_uncertain_alert() -> None:
    classifier = FilingClassifier(
        rules={
            "at_the_market": ("at-the-market offering", "sales agreement"),
            "private_placement": ("private placement", "PIPE"),
        }
    )

    result = classifier.classify("The registrant filed this prospectus supplement.")

    assert result.is_potentially_relevant is True
    assert result.classification == "uncertain"
    assert result.uncertain is True
    assert result.matched_evidence == ()


def test_classifier_returns_all_matching_evidence() -> None:
    classifier = FilingClassifier(
        rules={"registered_direct": ("registered direct offering", "placement agent")}
    )

    result = classifier.classify(
        "A registered direct offering was arranged through a placement agent."
    )

    assert result.classification == "registered_direct"
    assert result.uncertain is False
    assert result.matched_evidence == ("registered direct offering", "placement agent")


def test_edgar_discovery_reads_historical_submission_files_from_boundary() -> None:
    session = RoutingSession(
        {
            "CIK0000000001.json": {
                "name": "Example Corp",
                "filings": {
                    "recent": {
                        "accessionNumber": ["0000000001-26-000002"],
                        "filingDate": ["2026-08-18"],
                        "acceptanceDateTime": ["20260818170000"],
                        "form": ["10-Q"],
                        "primaryDocument": ["q.htm"],
                    },
                    "files": [
                        {
                            "name": "CIK0000000001-submissions-001.json",
                            "filingFrom": "2015-01-01",
                            "filingTo": "2025-12-31",
                        }
                    ],
                },
            },
            "CIK0000000001-submissions-001.json": {
                "accessionNumber": ["0000000001-20-000001", "0000000001-14-000001"],
                "filingDate": ["2020-06-01", "2014-06-01"],
                "acceptanceDateTime": ["20200601170000", "20140601170000"],
                "form": ["S-3", "S-3"],
                "primaryDocument": ["s3-2020.htm", "s3-2014.htm"],
            },
        }
    )
    client = EdgarClient(
        organization="Example Research LLC",
        contact_email="ops@example.test",
        candidate_forms=frozenset({"S-3"}),
        session=session,
    )

    filings = client.discover_filings(
        cik=1,
        symbol="EXM",
        filing_date_from=date(2016, 1, 1),
    )

    assert [filing.accession_number for filing in filings] == ["0000000001-20-000001"]
    assert session.urls[-1].endswith("/CIK0000000001-submissions-001.json")
