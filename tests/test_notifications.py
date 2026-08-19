"""Subscriber routing and injection-safe email composition contracts."""

import json
from datetime import UTC, date, datetime

import pytest

from closing_signal.data.repository import SQLiteRepository
from closing_signal.notify.email import EmailRenderer, NotificationContent
from closing_signal.notify.subscribers import SubscriberRegistry


def _subscriber(email: str, *, active: bool = True) -> dict[str, object]:
    return {
        "email": email,
        "active": active,
        "categories": ["strategy:moving_average_volume"],
        "consent_source": "double_opt_in_email",
        "policy_version": "privacy-v1",
        "consented_at": "2026-08-18T17:00:00+00:00",
        "confirmed_at": "2026-08-18T17:05:00+00:00",
        "deactivated_at": None if active else "2026-08-19T17:00:00+00:00",
        "deactivation_reason": None if active else "subscriber_request",
    }


def test_subscriber_registry_normalizes_filters_and_honors_deactivation(tmp_path) -> None:
    path = tmp_path / "subscribers.json"
    path.write_text(
        json.dumps(
            [
                _subscriber(" Trader@Example.COM "),
                _subscriber("off@example.com", active=False),
            ]
        ),
        encoding="utf-8",
    )

    registry = SubscriberRegistry.load(path)

    assert registry.recipients("strategy:moving_average_volume") == ("trader@example.com",)


def test_subscriber_registry_rejects_invalid_addresses(tmp_path) -> None:
    path = tmp_path / "subscribers.json"
    path.write_text(
        json.dumps([{"email": "not-an-email", "active": True, "categories": ["sec"]}]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="email"):
        SubscriberRegistry.load(path)


def test_active_subscriber_requires_double_opt_in_evidence(tmp_path) -> None:
    path = tmp_path / "subscribers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "email": "reader@example.com",
                    "active": True,
                    "categories": ["sec"],
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="consent evidence"):
        SubscriberRegistry.load(path)


def test_renderer_escapes_external_text_and_produces_both_alternatives() -> None:
    content = NotificationContent(
        category="sec",
        title="Offering <script>alert(1)</script>",
        occurred_on=date(2026, 8, 18),
        cutoff_at=datetime(2026, 8, 18, 22, tzinfo=UTC),
        status="complete",
        summary="Issuer & evidence",
        items=(
            {
                "symbol": "EXM",
                "classification": "uncertain",
                "evidence": "<img src=x onerror=alert(1)>",
            },
        ),
        source_links=("https://www.sec.gov/Archives/example.htm",),
        revision="1",
    )

    rendered = EmailRenderer(template_version="1").render(content)

    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    assert "<img" not in rendered.html
    assert "Offering <script>alert(1)</script>" in rendered.plain_text
    assert rendered.idempotency_material.endswith(":1:1")


def test_subscriber_state_changes_create_audit_events(tmp_path) -> None:
    path = tmp_path / "subscribers.json"
    path.write_text(
        json.dumps([_subscriber("reader@example.com") | {"categories": ["sec"]}]),
        encoding="utf-8",
    )
    repository = SQLiteRepository(tmp_path / "market.db")
    active = SubscriberRegistry.load(path)
    repository.sync_subscribers(active.subscribers)
    repository.sync_subscribers(active.subscribers)
    path.write_text(
        json.dumps([_subscriber("reader@example.com", active=False) | {"categories": ["sec"]}]),
        encoding="utf-8",
    )

    repository.sync_subscribers(SubscriberRegistry.load(path).subscribers)

    assert repository.count("subscribers") == 1
    assert repository.count("subscription_events") == 2
