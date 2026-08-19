"""Centrally managed, file-backed subscriber routing."""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class Subscriber:
    """Normalized subscriber state loaded outside application source code."""

    email: str
    active: bool
    categories: frozenset[str]
    consent_source: str
    policy_version: str
    consented_at: datetime
    confirmed_at: datetime
    deactivated_at: datetime | None
    deactivation_reason: str | None


class SubscriberRegistry:
    """Read-only routing view; state changes occur by replacing managed config."""

    def __init__(self, subscribers: tuple[Subscriber, ...]) -> None:
        self.subscribers = subscribers

    @classmethod
    def load(cls, path: str | Path) -> "SubscriberRegistry":
        """Load, normalize, and validate a JSON subscriber list."""
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            raise ValueError("subscriber file must contain a JSON list")
        subscribers: list[Subscriber] = []
        seen: set[str] = set()
        for raw in parsed:
            if not isinstance(raw, dict):
                raise ValueError("each subscriber must be an object")
            email = str(raw.get("email", "")).strip().lower()
            if not _EMAIL_PATTERN.fullmatch(email):
                raise ValueError(f"invalid subscriber email: {email}")
            if email in seen:
                raise ValueError(f"duplicate subscriber email: {email}")
            seen.add(email)
            active = raw.get("active")
            categories = raw.get("categories")
            if not isinstance(active, bool):
                raise ValueError(f"subscriber active state must be boolean: {email}")
            if (
                not isinstance(categories, list)
                or not categories
                or any(not isinstance(item, str) or not item.strip() for item in categories)
            ):
                raise ValueError(f"subscriber categories are invalid: {email}")
            consent_source = _required_text(raw, "consent_source")
            policy_version = _required_text(raw, "policy_version")
            consented_at = _required_timestamp(raw, "consented_at", email)
            confirmed_at = _required_timestamp(raw, "confirmed_at", email)
            deactivated_at = _optional_timestamp(raw, "deactivated_at", email)
            reason_value = raw.get("deactivation_reason")
            deactivation_reason = str(reason_value).strip() if reason_value is not None else None
            if confirmed_at < consented_at:
                raise ValueError(f"subscriber confirmation precedes consent: {email}")
            if active and (deactivated_at is not None or deactivation_reason is not None):
                raise ValueError(f"active subscriber has deactivation evidence: {email}")
            if not active and (deactivated_at is None or not deactivation_reason):
                raise ValueError(f"inactive subscriber requires deactivation evidence: {email}")
            if deactivated_at is not None and deactivated_at < confirmed_at:
                raise ValueError(f"subscriber deactivation precedes confirmation: {email}")
            subscribers.append(
                Subscriber(
                    email,
                    active,
                    frozenset(item.strip() for item in categories),
                    consent_source,
                    policy_version,
                    consented_at,
                    confirmed_at,
                    deactivated_at,
                    deactivation_reason,
                )
            )
        return cls(tuple(subscribers))

    def recipients(self, category: str) -> tuple[str, ...]:
        """Return active recipients explicitly subscribed to the category."""
        return tuple(
            sorted(
                subscriber.email
                for subscriber in self.subscribers
                if subscriber.active and category in subscriber.categories
            )
        )


def _required_text(raw: dict[object, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"subscriber consent evidence is missing: {field}")
    return value.strip()


def _required_timestamp(raw: dict[object, object], field: str, email: str) -> datetime:
    value = _optional_timestamp(raw, field, email)
    if value is None:
        raise ValueError(f"subscriber consent evidence is missing: {field}")
    return value


def _optional_timestamp(raw: dict[object, object], field: str, email: str) -> datetime | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"subscriber timestamp is invalid: {email} {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"subscriber timestamp is invalid: {email} {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"subscriber timestamp must include timezone: {email} {field}")
    return parsed
