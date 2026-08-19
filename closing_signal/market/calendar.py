"""Exchange-aware completion rules for end-of-day processing."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True, slots=True)
class MarketSession:
    """A provider-supplied U.S. exchange session, including early closes."""

    session_date: date
    open_at: datetime
    close_at: datetime

    def __post_init__(self) -> None:
        if self.open_at.tzinfo is None or self.close_at.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        if self.close_at <= self.open_at:
            raise ValueError("session close must follow session open")


class ExchangeCalendar:
    """Determine the latest safely finalized provider session."""

    def __init__(self, *, finalization_delay: timedelta) -> None:
        if finalization_delay < timedelta(0):
            raise ValueError("finalization_delay cannot be negative")
        self.finalization_delay = finalization_delay

    def latest_completed_session(
        self, sessions: Iterable[MarketSession], *, now: datetime
    ) -> MarketSession | None:
        """Return the newest session whose configured finalization time passed."""
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        completed = [
            session for session in sessions if now >= session.close_at + self.finalization_delay
        ]
        return max(completed, key=lambda session: session.session_date, default=None)
