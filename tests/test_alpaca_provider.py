"""Alpaca adapter contract tests using an in-memory HTTP boundary."""

from collections.abc import Mapping
from datetime import date
from typing import Any

from closing_signal.core.http import RetryPolicy
from closing_signal.domain.models import Exchange, InstrumentType
from closing_signal.providers.alpaca import AlpacaClient, ExplicitAssetClassifier


class StubResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class StubSession:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, Any] | None]] = []
        self.headers: dict[str, str] = {}

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> StubResponse:
        del timeout
        self.calls.append((url, params))
        return StubResponse(self.responses.pop(0))


def test_assets_filter_venues_types_and_reject_unclassified() -> None:
    session = StubSession(
        [
            [
                {
                    "id": "1",
                    "symbol": "BRK.B",
                    "name": "Berkshire Hathaway",
                    "exchange": "NYSE",
                    "status": "active",
                    "tradable": True,
                    "security_type": "common_stock",
                },
                {
                    "id": "2",
                    "symbol": "QQQ",
                    "name": "Invesco QQQ",
                    "exchange": "NASDAQ",
                    "status": "active",
                    "tradable": True,
                    "security_type": "etf",
                },
                {
                    "id": "3",
                    "symbol": "OTCM",
                    "name": "Outside universe",
                    "exchange": "OTC",
                    "status": "active",
                    "tradable": True,
                    "security_type": "common_stock",
                },
                {
                    "id": "4",
                    "symbol": "UNKNOWN",
                    "name": "Unclassified",
                    "exchange": "NYSE",
                    "status": "active",
                    "tradable": True,
                },
            ]
        ]
    )
    client = AlpacaClient(
        api_key="key",
        api_secret="secret",
        feed="sip",
        session=session,
        classifier=ExplicitAssetClassifier(),
    )

    result = client.fetch_instruments(observed_on=date(2026, 8, 18))

    assert [(item.exchange, item.instrument_type) for item in result.accepted] == [
        (Exchange.NYSE, InstrumentType.COMMON_STOCK),
        (Exchange.NASDAQ, InstrumentType.ETF),
    ]
    assert {item.provider_symbol for item in result.rejected} == {"OTCM", "UNKNOWN"}
    assert session.headers["APCA-API-KEY-ID"] == "key"
    assert session.headers["APCA-API-SECRET-KEY"] == "secret"


def test_daily_bars_follow_page_tokens_and_preserve_source_timestamp() -> None:
    session = StubSession(
        [
            {
                "bars": {
                    "AAPL": [
                        {
                            "t": "2026-08-17T04:00:00Z",
                            "o": 100,
                            "h": 104,
                            "l": 99,
                            "c": 103,
                            "v": 1200,
                        }
                    ]
                },
                "next_page_token": "page-2",
            },
            {
                "bars": {
                    "MSFT": [
                        {
                            "t": "2026-08-17T04:00:00Z",
                            "o": 200,
                            "h": 205,
                            "l": 198,
                            "c": 204,
                            "v": 500,
                        }
                    ]
                },
                "next_page_token": None,
            },
        ]
    )
    client = AlpacaClient(
        api_key="key",
        api_secret="secret",
        feed="iex",
        session=session,
        classifier=ExplicitAssetClassifier(),
    )

    bars = list(
        client.fetch_daily_bars(
            symbols=["AAPL", "MSFT"],
            start=date(2026, 8, 17),
            end=date(2026, 8, 17),
        )
    )

    assert [bar.instrument_id for bar in bars] == ["AAPL", "MSFT"]
    assert all(bar.feed == "iex" for bar in bars)
    assert bars[0].source_timestamp.isoformat() == "2026-08-17T04:00:00+00:00"
    assert session.calls[1][1] is not None
    assert session.calls[1][1]["page_token"] == "page-2"


def test_calendar_parses_regular_and_early_closes_in_new_york_time() -> None:
    session = StubSession(
        [
            [
                {"date": "2026-07-02", "open": "09:30", "close": "16:00"},
                {"date": "2026-07-03", "open": "09:30", "close": "13:00"},
            ]
        ]
    )
    client = AlpacaClient(
        api_key="key",
        api_secret="secret",
        feed="iex",
        session=session,
        classifier=ExplicitAssetClassifier(),
    )

    sessions = client.fetch_calendar(start=date(2026, 7, 2), end=date(2026, 7, 3))

    assert sessions[0].close_at.isoformat() == "2026-07-02T16:00:00-04:00"
    assert sessions[1].close_at.isoformat() == "2026-07-03T13:00:00-04:00"


def test_corporate_actions_follow_pages_and_preserve_provider_payload() -> None:
    session = StubSession(
        [
            {
                "corporate_actions": {
                    "forward_splits": [
                        {
                            "id": "ca-1",
                            "symbol": "EXM",
                            "ex_date": "2026-08-17",
                            "process_date": "2026-08-18",
                            "rate": "2",
                        }
                    ],
                    "cash_dividends": [],
                },
                "next_page_token": "next",
            },
            {
                "corporate_actions": {
                    "name_changes": [
                        {
                            "id": "ca-2",
                            "symbol": "OLD",
                            "new_symbol": "NEW",
                            "effective_date": "2026-08-18",
                        }
                    ]
                },
                "next_page_token": None,
            },
        ]
    )
    client = AlpacaClient(
        api_key="key",
        api_secret="secret",
        feed="sip",
        session=session,
        classifier=ExplicitAssetClassifier(),
    )

    actions = list(client.fetch_corporate_actions(start=date(2026, 8, 17), end=date(2026, 8, 18)))

    assert [action.provider_action_id for action in actions] == ["ca-1", "ca-2"]
    assert actions[0].action_type == "forward_split"
    assert actions[0].ratio == 2
    assert actions[1].new_symbol == "NEW"
    assert session.calls[1][1] is not None
    assert session.calls[1][1]["page_token"] == "next"


class FailFirstSession(StubSession):
    def __init__(self, payload: object) -> None:
        super().__init__([payload])
        self.failed = False

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None, timeout: float
    ) -> StubResponse:
        if not self.failed:
            self.failed = True
            raise ConnectionError("temporary")
        return super().get(url, params=params, timeout=timeout)


def test_transient_http_failure_uses_bounded_retry_policy() -> None:
    session = FailFirstSession([])
    client = AlpacaClient(
        api_key="key",
        api_secret="secret",
        feed="sip",
        session=session,
        classifier=ExplicitAssetClassifier(),
        retry_policy=RetryPolicy(
            max_attempts=2,
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
            jitter_seconds=0.0,
        ),
    )

    result = client.fetch_instruments(observed_on=date(2026, 8, 18))

    assert result.accepted == ()
    assert session.failed is True
