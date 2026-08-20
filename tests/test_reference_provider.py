"""OpenFIGI primary classification with Nasdaq and SEC reconciliation."""

from collections.abc import Mapping

from closing_signal.domain.models import Exchange, InstrumentType
from closing_signal.providers.reference import (
    NasdaqReference,
    OpenFigiClient,
    ReconciledAssetClassifier,
    ReferenceClassificationResult,
    SECReference,
    parse_nasdaq_directories,
    parse_sec_company_tickers,
)


class StubResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self.payload


class StubSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}
        self.posts: list[tuple[str, object]] = []

    def post(self, url: str, *, json: object, timeout: float) -> StubResponse:
        del timeout
        self.posts.append((url, json))
        return StubResponse(self.responses.pop(0))


def _asset(symbol: str, exchange: str = "NASDAQ") -> dict[str, object]:
    return {"symbol": symbol, "exchange": exchange}


def test_openfigi_maps_explicit_types_and_refuses_ambiguous_results() -> None:
    session = StubSession(
        [
            [
                {"data": [{"securityType": "Common Stock", "securityType2": "Common Stock"}]},
                {"data": [{"securityType": "ETP", "securityType2": "Mutual Fund"}]},
                {
                    "data": [
                        {
                            "securityType": "Depositary Receipt",
                            "securityType2": "Common Stock",
                        }
                    ]
                },
                {
                    "data": [
                        {"securityType": "ETP"},
                        {"securityType": "Common Stock"},
                    ]
                },
            ]
        ]
    )
    client = OpenFigiClient(api_key="openfigi-key", session=session, request_interval=0)

    result = client.fetch_classifications(
        [_asset("AAPL"), _asset("QQQ"), _asset("BABA", "NYSE"), _asset("AMB")]
    )

    assert result.classifications == {
        "AAPL": InstrumentType.COMMON_STOCK,
        "QQQ": InstrumentType.ETF,
        "BABA": InstrumentType.ADR,
    }
    assert result.issues == {"AMB": "OpenFIGI returned conflicting security types"}
    assert session.headers["X-OPENFIGI-APIKEY"] == "openfigi-key"
    jobs = session.posts[0][1]
    assert isinstance(jobs, list)
    assert jobs[0] == {
        "idType": "TICKER",
        "idValue": "AAPL",
        "exchCode": "US",
        "marketSecDes": "Equity",
    }


def test_openfigi_preserves_actionable_provider_errors_and_warnings() -> None:
    session = StubSession(
        [[{"error": "securityType2 required"}, {"warning": "No identifier found."}]]
    )
    client = OpenFigiClient(api_key="openfigi-key", session=session, request_interval=0)

    result = client.fetch_classifications([_asset("AAPL"), _asset("MISSING")])

    assert result.issues == {
        "AAPL": "OpenFIGI API error: securityType2 required",
        "MISSING": "OpenFIGI API warning: No identifier found.",
    }


def test_openfigi_accepts_valid_data_returned_with_a_warning() -> None:
    session = StubSession(
        [
            [
                {
                    "warning": "Additional provider context.",
                    "data": [{"securityType": "Common Stock"}],
                }
            ]
        ]
    )
    client = OpenFigiClient(api_key="openfigi-key", session=session, request_interval=0)

    result = client.fetch_classifications([_asset("AAPL")])

    assert result.classifications == {"AAPL": InstrumentType.COMMON_STOCK}
    assert result.issues == {}


def test_openfigi_reports_bounded_batch_progress() -> None:
    assets = [_asset(f"SYM{index:03}") for index in range(101)]
    mapped = {"data": [{"securityType": "Common Stock"}]}
    session = StubSession([[mapped] * 100, [mapped]])
    events = []
    client = OpenFigiClient(
        api_key="openfigi-key",
        session=session,
        request_interval=0,
        progress=events.append,
    )

    client.fetch_classifications(assets)

    assert [(event.completed, event.total, event.unit) for event in events] == [
        (1, 2, "batches"),
        (2, 2, "batches"),
    ]


class PrimarySource:
    def fetch_classifications(
        self, assets: list[Mapping[str, object]]
    ) -> ReferenceClassificationResult:
        del assets
        return ReferenceClassificationResult(
            {
                "AAPL": InstrumentType.COMMON_STOCK,
                "QQQ": InstrumentType.COMMON_STOCK,
                "BABA": InstrumentType.ADR,
            },
            {},
        )


class NasdaqSource:
    def fetch_references(self) -> Mapping[str, NasdaqReference]:
        return {
            "AAPL": NasdaqReference(Exchange.NASDAQ, is_etf=False),
            "QQQ": NasdaqReference(Exchange.NASDAQ, is_etf=True),
            "BABA": NasdaqReference(Exchange.NYSE, is_etf=False),
        }


class SECSource:
    def fetch_references(self) -> Mapping[str, SECReference]:
        return {
            "AAPL": SECReference(Exchange.NASDAQ),
            "BABA": SECReference(Exchange.NASDAQ),
        }


def test_reconciler_rejects_primary_type_and_exchange_conflicts() -> None:
    events = []
    classifier = ReconciledAssetClassifier(
        PrimarySource(), NasdaqSource(), SECSource(), progress=events.append
    )
    assets = [_asset("AAPL"), _asset("QQQ"), _asset("BABA", "NYSE")]

    classifier.prepare(assets)

    assert classifier.classify(assets[0]) is InstrumentType.COMMON_STOCK
    assert classifier.classify(assets[1]) is None
    assert classifier.failure_reason(assets[1]) == "OpenFIGI type conflicts with Nasdaq ETF flag"
    assert classifier.classify(assets[2]) is None
    assert classifier.failure_reason(assets[2]) == "Alpaca venue conflicts with SEC association"
    assert [event.message for event in events] == [
        "Classifying the Alpaca catalog with OpenFIGI",
        "Reconciling the Nasdaq listing directories",
        "Reconciling SEC company ticker associations",
    ]


def test_reference_parsers_keep_only_nyse_nasdaq_non_test_symbols() -> None:
    nasdaq = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF\nAAPL|Apple|Q|N|N|100|N\nQQQ|Fund|Q|N|N|100|Y\nFile Creation Time: 0818202617:03||||||\n"
    other = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\nBABA|ADR|N|BABA|N|100|N|BABA\nSPY|Fund|P|SPY|Y|100|N|SPY\n"

    references = parse_nasdaq_directories(nasdaq, other)

    assert references == {
        "AAPL": NasdaqReference(Exchange.NASDAQ, is_etf=False),
        "QQQ": NasdaqReference(Exchange.NASDAQ, is_etf=True),
        "BABA": NasdaqReference(Exchange.NYSE, is_etf=False),
    }

    sec = parse_sec_company_tickers(
        {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [
                [320193, "Apple", "AAPL", "Nasdaq"],
                [1652044, "Alphabet", "GOOG", "Nasdaq"],
                [1, "Other", "OTHER", "OTC"],
            ],
        }
    )
    assert sec == {
        "AAPL": SECReference(Exchange.NASDAQ),
        "GOOG": SECReference(Exchange.NASDAQ),
    }
