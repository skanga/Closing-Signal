"""Idempotent, retrying email delivery with per-recipient audit records."""

import hashlib
import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as datetime_time
from email.message import EmailMessage
from typing import Protocol

from closing_signal.notify.email import RenderedEmail


class DeliveryRepository(Protocol):
    """Minimal persistence boundary required by notification delivery."""

    def notification_status(self, delivery_key: str) -> str | None: ...

    def notification_attempt_count_since(self, since: datetime) -> int: ...

    def record_notification_attempt(
        self,
        *,
        delivery_key: str,
        recipient: str,
        template_version: str,
        status: str,
        provider_response: str | None,
        attempted_at: datetime,
    ) -> None: ...


class EmailTransport(Protocol):
    """Provider-neutral transport used by the delivery coordinator."""

    def send(self, *, sender: str, recipient: str, message: RenderedEmail) -> str: ...


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Per-invocation recipient outcomes."""

    succeeded: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    dry_run: tuple[str, ...] = ()


class EmailDeliveryService:
    """Retry transient failures without resending already-successful recipients."""

    def __init__(
        self,
        *,
        repository: DeliveryRepository,
        transport: EmailTransport,
        sender: str,
        max_attempts: int,
        backoff_seconds: tuple[float, ...],
        daily_send_limit: int | None = None,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if any(delay < 0 for delay in backoff_seconds):
            raise ValueError("backoff delays cannot be negative")
        if daily_send_limit is not None and daily_send_limit < 1:
            raise ValueError("daily_send_limit must be positive")
        self.repository = repository
        self.transport = transport
        self.sender = sender
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.daily_send_limit = daily_send_limit
        self.now = now
        self.sleep = sleep

    def deliver(
        self,
        message: RenderedEmail,
        *,
        recipients: tuple[str, ...],
        dry_run: bool = False,
    ) -> DeliveryResult:
        """Deliver once per normalized recipient and deterministic result revision."""
        succeeded: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        dry: list[str] = []
        for recipient in sorted({address.strip().lower() for address in recipients}):
            if dry_run:
                dry.append(recipient)
                continue
            delivery_key = _delivery_key(message.idempotency_material, recipient)
            if self.repository.notification_status(delivery_key) == "succeeded":
                skipped.append(recipient)
                continue
            delivered = False
            for attempt in range(self.max_attempts):
                attempted_at = self.now()
                if self._daily_send_limit_reached(attempted_at):
                    self.repository.record_notification_attempt(
                        delivery_key=delivery_key,
                        recipient=recipient,
                        template_version=message.template_version,
                        status="failed",
                        provider_response="DailySendLimitExceeded",
                        attempted_at=attempted_at,
                    )
                    break
                try:
                    response = self.transport.send(
                        sender=self.sender, recipient=recipient, message=message
                    )
                except (ConnectionError, OSError, smtplib.SMTPException) as exc:
                    self.repository.record_notification_attempt(
                        delivery_key=delivery_key,
                        recipient=recipient,
                        template_version=message.template_version,
                        status="failed",
                        provider_response=type(exc).__name__,
                        attempted_at=self.now(),
                    )
                    if attempt + 1 < self.max_attempts:
                        delay_index = min(attempt, len(self.backoff_seconds) - 1)
                        delay = self.backoff_seconds[delay_index] if delay_index >= 0 else 0
                        self.sleep(delay)
                    continue
                self.repository.record_notification_attempt(
                    delivery_key=delivery_key,
                    recipient=recipient,
                    template_version=message.template_version,
                    status="succeeded",
                    provider_response=response,
                    attempted_at=self.now(),
                )
                succeeded.append(recipient)
                delivered = True
                break
            if not delivered:
                failed.append(recipient)
        return DeliveryResult(tuple(succeeded), tuple(failed), tuple(skipped), tuple(dry))

    def _daily_send_limit_reached(self, attempted_at: datetime) -> bool:
        if self.daily_send_limit is None:
            return False
        utc_day = attempted_at.astimezone(UTC).date()
        day_start = datetime.combine(utc_day, datetime_time.min, tzinfo=UTC)
        return self.repository.notification_attempt_count_since(day_start) >= self.daily_send_limit


def _delivery_key(material: str, recipient: str) -> str:
    return hashlib.sha256(f"{material}\0{recipient}".encode()).hexdigest()


class SMTPTransport:
    """Standard SMTP implementation selected only when explicitly configured."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        use_ssl: bool,
        starttls: bool,
        timeout: float = 30.0,
    ) -> None:
        if use_ssl and starttls:
            raise ValueError("use_ssl and starttls are mutually exclusive")
        if bool(username) != bool(password):
            raise ValueError("SMTP username and password must be supplied together")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.starttls = starttls
        self.timeout = timeout

    def send(self, *, sender: str, recipient: str, message: RenderedEmail) -> str:
        """Send one multipart alternative message without attachments."""
        email = EmailMessage()
        email["From"] = sender
        email["To"] = recipient
        email["Subject"] = message.subject
        email.set_content(message.plain_text)
        email.add_alternative(message.html, subtype="html")
        client_type = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with client_type(self.host, self.port, timeout=self.timeout) as client:
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                client.login(self.username, self.password)
            refused = client.send_message(email)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
        return "accepted"

    def probe(self) -> None:
        """Open, secure, authenticate, and NOOP the configured SMTP transport."""
        client_type = smtplib.SMTP_SSL if self.use_ssl else smtplib.SMTP
        with client_type(self.host, self.port, timeout=self.timeout) as client:
            if self.starttls:
                client.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                client.login(self.username, self.password)
            code, _ = client.noop()
        if code >= 400:
            raise smtplib.SMTPResponseException(code, b"SMTP health check failed")
