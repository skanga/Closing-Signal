"""Provider- and storage-independent point-in-time strategy framework."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

type MetricValue = Decimal | int | str | bool


@dataclass(frozen=True, slots=True)
class SignalBar:
    """Split-adjusted price inputs and raw volume available to strategies."""

    symbol: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    dollar_volume: Decimal
    total_return_close: Decimal | None = None

    def __post_init__(self) -> None:
        prices = (self.open, self.high, self.low, self.close)
        if not self.symbol or any(price < 0 or not price.is_finite() for price in prices):
            raise ValueError("signal bar has invalid identity or price")
        if self.high < max(prices) or self.low > min(prices):
            raise ValueError("signal bar has inconsistent OHLC values")
        if self.volume < 0 or self.dollar_volume < 0:
            raise ValueError("signal bar volume values cannot be negative")
        if self.total_return_close is not None and self.total_return_close < 0:
            raise ValueError("total_return_close cannot be negative")


class PointInTimeDataView:
    """Read-only market view that excludes every bar after the decision cutoff."""

    def __init__(
        self,
        bars_by_symbol: Mapping[str, Iterable[SignalBar]],
        *,
        cutoff_session: date,
        cutoff_at: datetime,
        universe_snapshot_id: str,
    ) -> None:
        if cutoff_at.tzinfo is None:
            raise ValueError("cutoff_at must be timezone-aware")
        if not universe_snapshot_id:
            raise ValueError("universe_snapshot_id is required")
        self.cutoff_session = cutoff_session
        self.cutoff_at = cutoff_at
        self.universe_snapshot_id = universe_snapshot_id
        self._bars = {
            symbol: tuple(
                sorted(
                    (bar for bar in values if bar.session_date <= cutoff_session),
                    key=lambda bar: bar.session_date,
                )
            )
            for symbol, values in bars_by_symbol.items()
        }

    @property
    def symbols(self) -> tuple[str, ...]:
        """Return deterministic universe ordering."""
        return tuple(sorted(self._bars))

    def bars(self, symbol: str) -> tuple[SignalBar, ...]:
        """Return only bars available on or before the declared cutoff."""
        return self._bars.get(symbol, ())


@dataclass(frozen=True, slots=True)
class StrategySelection:
    """A ranked, explainable strategy match."""

    symbol: str
    rank: int
    matched_conditions: tuple[str, ...]
    metrics: Mapping[str, MetricValue]


class StrategyStatus(StrEnum):
    """Persisted distinction between success, empty success, and failure."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    NO_SELECTIONS = "no_selections"
    FAILED = "failed"


class Strategy(Protocol):
    """Common strategy interface independent of providers, SQL, and delivery."""

    @property
    def strategy_id(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def parameters(self) -> Mapping[str, object]: ...

    @property
    def minimum_history_sessions(self) -> int: ...

    def evaluate(self, view: PointInTimeDataView) -> list[StrategySelection]: ...


@dataclass(frozen=True, slots=True)
class StrategyResult:
    """Structured result and diagnostics for one strategy execution."""

    strategy_id: str
    strategy_version: str
    parameters: Mapping[str, object]
    universe_snapshot_id: str
    input_cutoff: datetime
    session_date: date
    status: StrategyStatus
    selections: tuple[StrategySelection, ...]
    symbols_evaluated: int
    symbols_skipped: int
    error: str | None = None


class StrategyRunner:
    """Run one strategy while converting a strategy-level exception to state."""

    def run(self, strategy: Strategy, view: PointInTimeDataView) -> StrategyResult:
        """Evaluate and return a result that never conflates empty with failure."""
        minimum_history = int(getattr(strategy, "minimum_history_sessions", 0))
        eligible = sum(len(view.bars(symbol)) >= minimum_history for symbol in view.symbols)
        skipped = len(view.symbols) - eligible
        try:
            if view.symbols and eligible == 0 and minimum_history > 0:
                raise ValueError(
                    f"insufficient history: strategy requires {minimum_history} sessions"
                )
            selections = tuple(strategy.evaluate(view))
            if skipped:
                status = StrategyStatus.PARTIAL
                error = (
                    f"{skipped} symbols skipped because fewer than "
                    f"{minimum_history} sessions were available"
                )
            else:
                status = StrategyStatus.COMPLETE if selections else StrategyStatus.NO_SELECTIONS
                error = None
        except Exception as exc:
            selections = ()
            status = StrategyStatus.FAILED
            error = str(exc)
        return StrategyResult(
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            parameters=strategy.parameters,
            universe_snapshot_id=view.universe_snapshot_id,
            input_cutoff=view.cutoff_at,
            session_date=view.cutoff_session,
            status=status,
            selections=selections,
            symbols_evaluated=eligible,
            symbols_skipped=skipped,
            error=error,
        )
