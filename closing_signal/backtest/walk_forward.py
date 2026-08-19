"""Deterministic rolling walk-forward window construction."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt


class WalkForwardConfig(BaseModel):
    """Explicit train/validation/test/step lengths in exchange sessions."""

    train_sessions: PositiveInt
    validation_sessions: PositiveInt
    test_sessions: PositiveInt
    step_sessions: PositiveInt
    mode: Literal["rolling", "anchored"]
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """Disjoint chronological segments for one rolling evaluation."""

    train: tuple[date, ...]
    validation: tuple[date, ...]
    test: tuple[date, ...]


def split_walk_forward(
    sessions: list[date], config: WalkForwardConfig
) -> tuple[WalkForwardWindow, ...]:
    """Create rolling windows without allowing test dates into selection data."""
    ordered = sorted(set(sessions))
    windows: list[WalkForwardWindow] = []
    step = 0
    while True:
        start = step * config.step_sessions if config.mode == "rolling" else 0
        train_end = (
            start + config.train_sessions
            if config.mode == "rolling"
            else config.train_sessions + step * config.step_sessions
        )
        validation_end = train_end + config.validation_sessions
        test_end = validation_end + config.test_sessions
        if test_end > len(ordered):
            break
        windows.append(
            WalkForwardWindow(
                tuple(ordered[start:train_end]),
                tuple(ordered[train_end:validation_end]),
                tuple(ordered[validation_end:test_end]),
            )
        )
        step += 1
    return tuple(windows)
