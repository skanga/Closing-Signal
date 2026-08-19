"""Idempotent per-recipient email delivery and retry contracts."""

from datetime import UTC, datetime

from closing_signal.data.repository import SQLiteRepository
from closing_signal.notify.delivery import EmailDeliveryService, SMTPTransport
from closing_signal.notify.email import RenderedEmail


class FlakyTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send(self, *, sender: str, recipient: str, message: RenderedEmail) -> str:
        del sender, message
        self.calls.append(recipient)
        if len(self.calls) == 1:
            raise ConnectionError("temporary SMTP failure")
        return "provider-message-1"


def _message() -> RenderedEmail:
    return RenderedEmail(
        subject="Daily results",
        plain_text="plain",
        html="<p>plain</p>",
        idempotency_material="strategy:2026-08-18:revision-1:template-1",
        template_version="1",
    )


def test_transient_failure_retries_and_success_is_never_resent(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    transport = FlakyTransport()
    service = EmailDeliveryService(
        repository=repository,
        transport=transport,
        sender="alerts@example.test",
        max_attempts=2,
        backoff_seconds=(0.0,),
        now=lambda: datetime(2026, 8, 18, 22, tzinfo=UTC),
    )

    first = service.deliver(_message(), recipients=("reader@example.test",))
    second = service.deliver(_message(), recipients=("reader@example.test",))

    assert first.succeeded == ("reader@example.test",)
    assert first.failed == ()
    assert second.skipped == ("reader@example.test",)
    assert transport.calls == ["reader@example.test", "reader@example.test"]
    assert repository.count("notification_deliveries") == 1
    assert repository.count("notification_attempts") == 2


def test_dry_run_does_not_send_or_claim_delivery(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    transport = FlakyTransport()
    service = EmailDeliveryService(
        repository=repository,
        transport=transport,
        sender="alerts@example.test",
        max_attempts=1,
        backoff_seconds=(),
    )

    result = service.deliver(_message(), recipients=("reader@example.test",), dry_run=True)

    assert result.dry_run == ("reader@example.test",)
    assert transport.calls == []
    assert repository.count("notification_deliveries") == 0


def test_partial_recipient_retry_never_resends_successes(tmp_path) -> None:
    class RecipientTransport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def send(self, *, sender: str, recipient: str, message: RenderedEmail) -> str:
            del sender, message
            self.calls.append(recipient)
            if recipient == "bad@example.test":
                raise ConnectionError("temporary SMTP failure")
            return "accepted"

    repository = SQLiteRepository(tmp_path / "market.db")
    transport = RecipientTransport()
    service = EmailDeliveryService(
        repository=repository,
        transport=transport,
        sender="alerts@example.test",
        max_attempts=1,
        backoff_seconds=(),
    )

    first = service.deliver(_message(), recipients=("good@example.test", "bad@example.test"))
    second = service.deliver(_message(), recipients=("good@example.test", "bad@example.test"))

    assert first.succeeded == ("good@example.test",)
    assert first.failed == ("bad@example.test",)
    assert second.skipped == ("good@example.test",)
    assert second.failed == ("bad@example.test",)
    assert transport.calls.count("good@example.test") == 1
    assert transport.calls.count("bad@example.test") == 2


def test_daily_provider_send_limit_stops_before_transport(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    transport = FlakyTransport()
    fixed_now = datetime(2026, 8, 18, 22, tzinfo=UTC)
    service = EmailDeliveryService(
        repository=repository,
        transport=transport,
        sender="alerts@example.test",
        max_attempts=2,
        backoff_seconds=(0.0,),
        daily_send_limit=1,
        now=lambda: fixed_now,
    )

    result = service.deliver(_message(), recipients=("reader@example.test",))

    assert result.failed == ("reader@example.test",)
    assert transport.calls == ["reader@example.test"]
    assert repository.count("notification_attempts") == 2


def test_smtp_transport_builds_multipart_and_authenticates(monkeypatch) -> None:
    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout) -> None:
            calls.append((host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            del args

        def starttls(self, *, context) -> None:
            calls.append(("starttls", context is not None))

        def login(self, username, password) -> None:
            calls.append(("login", username, password))

        def send_message(self, email):
            calls.append((email["To"], email.is_multipart()))
            return {}

    monkeypatch.setattr("closing_signal.notify.delivery.smtplib.SMTP", FakeSMTP)
    transport = SMTPTransport(
        host="smtp.example.com",
        port=587,
        username="user",
        password="secret",
        use_ssl=False,
        starttls=True,
    )

    response = transport.send(
        sender="alerts@example.com", recipient="reader@example.com", message=_message()
    )

    assert response == "accepted"
    assert ("login", "user", "secret") in calls
    assert ("reader@example.com", True) in calls


def test_smtp_probe_secures_authenticates_and_checks_server(monkeypatch) -> None:
    calls: list[object] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout) -> None:
            calls.append((host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            del args

        def starttls(self, *, context) -> None:
            calls.append(("starttls", context is not None))

        def login(self, username, password) -> None:
            calls.append(("login", username, password))

        def noop(self):
            calls.append("noop")
            return 250, b"ok"

    monkeypatch.setattr("closing_signal.notify.delivery.smtplib.SMTP", FakeSMTP)
    transport = SMTPTransport(
        host="smtp.example.com",
        port=587,
        username="user",
        password="secret",
        use_ssl=False,
        starttls=True,
    )

    transport.probe()

    assert ("starttls", True) in calls
    assert ("login", "user", "secret") in calls
    assert "noop" in calls
