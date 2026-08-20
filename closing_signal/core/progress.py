"""Bounded progress events for interactive operators and scheduler logs."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One sanitized human-readable milestone with optional bounded counts."""

    message: str
    completed: int | None = None
    total: int | None = None
    unit: str | None = None

    def render(self) -> str:
        if self.completed is None:
            return self.message
        count = f"{self.completed:,}"
        if self.total is not None:
            count = f"{count}/{self.total:,}"
        if self.unit:
            count = f"{count} {self.unit}"
        return f"{self.message}: {count}"


class ProgressReporter(Protocol):
    """Callback boundary used by long-running providers and workflows."""

    def __call__(self, event: ProgressEvent) -> None: ...


def no_progress(event: ProgressEvent) -> None:
    """Default reporter for library and unit-test callers."""
    del event


class StderrProgressReporter:
    """Write immediately visible progress without contaminating stdout results."""

    def __init__(self, operation: str) -> None:
        self.operation = operation

    def __call__(self, event: ProgressEvent) -> None:
        stream = sys.stderr
        if stream is None:
            return
        try:
            print(f"[{self.operation}] {event.render()}", file=stream, flush=True)
        except Exception:
            return


def should_report(completed: int, *, total: int, every: int) -> bool:
    """Bound logs while always reporting the first and final units."""
    if completed < 1 or total < 1 or every < 1:
        raise ValueError("progress counts and interval must be positive")
    return completed == 1 or completed == total or completed % every == 0
