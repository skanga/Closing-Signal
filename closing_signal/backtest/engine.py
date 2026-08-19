"""A deterministic EOD portfolio simulator that reuses production strategies."""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from closing_signal.strategy.framework import PointInTimeDataView, SignalBar, Strategy


class ExecutionConvention(StrEnum):
    """Supported fills that occur strictly after an EOD signal."""

    NEXT_OPEN = "next_session_open"
    NEXT_CLOSE = "next_session_close"


class BacktestConfig(BaseModel):
    """Complete reproducibility and economic assumptions for a single run."""

    start: date
    end: date
    benchmark_symbol: str
    execution: ExecutionConvention
    initial_capital: Decimal = Field(gt=0)
    position_size_fraction: Decimal = Field(gt=0, le=1)
    holding_sessions: PositiveInt
    fixed_fee: Decimal = Field(ge=0)
    per_share_fee: Decimal = Field(ge=0)
    percentage_fee: Decimal = Field(ge=0)
    minimum_fee: Decimal = Field(ge=0)
    slippage_bps: Decimal = Field(ge=0)
    annual_risk_free_rate: Decimal
    random_seed: int
    evaluation_segment: Literal["in_sample", "validation", "out_of_sample"]
    rebalance_rule: Literal["daily"]
    holding_rule: Literal["fixed_sessions"]
    position_sizing: Literal["fraction_of_initial_capital"]
    missing_exit_policy: Literal["fail", "mark_zero"]
    strategy_config_version: str
    universe_version: str
    data_version: str
    code_version: str
    model_config = ConfigDict(frozen=True, extra="forbid")

    @model_validator(mode="after")
    def dates_and_versions_are_valid(self) -> "BacktestConfig":
        if self.end < self.start:
            raise ValueError("backtest end cannot precede start")
        required = (
            self.benchmark_symbol,
            self.strategy_config_version,
            self.universe_version,
            self.data_version,
            self.code_version,
        )
        if any(not item.strip() for item in required):
            raise ValueError("benchmark and reproducibility versions cannot be blank")
        return self


@dataclass(frozen=True, slots=True)
class FeeModel:
    """Composable commission model applied independently to each side."""

    fixed: Decimal
    per_share: Decimal
    percentage: Decimal
    minimum: Decimal

    def calculate(self, *, shares: int, price: Decimal) -> Decimal:
        if shares < 0 or price < 0:
            raise ValueError("fee inputs cannot be negative")
        calculated = self.fixed + self.per_share * shares + self.percentage * price * shares
        return max(calculated, self.minimum)


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    signal_date: date
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    shares: int
    entry_fee: Decimal
    exit_fee: Decimal
    dividends: Decimal
    net_pnl: Decimal

    @property
    def total_fees(self) -> Decimal:
        return self.entry_fee + self.exit_fee


@dataclass(frozen=True, slots=True)
class EquityPoint:
    session_date: date
    equity: Decimal
    cash: Decimal
    market_value: Decimal


@dataclass(frozen=True, slots=True)
class PositionPoint:
    session_date: date
    symbol: str
    shares: int
    market_price: Decimal
    market_value: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_return: Decimal
    annualized_return: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    maximum_drawdown: Decimal
    calmar_ratio: Decimal
    hit_rate: Decimal
    exposure: Decimal
    turnover: Decimal
    trade_count: int
    average_holding_period: Decimal
    benchmark_return: Decimal
    benchmark_relative_return: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: BacktestConfig
    strategy_id: str
    strategy_version: str
    trades: tuple[Trade, ...]
    positions: tuple[PositionPoint, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: BacktestMetrics
    warnings: tuple[str, ...]
    manifest: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class BacktestProgress:
    """One deterministic session-completion event for long-running evaluations."""

    session_date: date
    completed_sessions: int
    total_sessions: int
    evaluation_segment: Literal["in_sample", "validation", "out_of_sample"]


@dataclass(slots=True)
class _Position:
    symbol: str
    signal_date: date
    entry_date: date
    entry_index: int
    entry_price: Decimal
    shares: int
    entry_fee: Decimal
    dividends: Decimal = Decimal(0)


class BacktestEngine:
    """Simulate signals, next-session fills, costs, cash, and marked equity."""

    def run(
        self,
        strategy: Strategy,
        bars_by_symbol: Mapping[str, Sequence[SignalBar]],
        universe_snapshots: Mapping[date, frozenset[str]],
        config: BacktestConfig,
        cash_dividends: Mapping[tuple[str, date], Decimal] | None = None,
        progress: Callable[[BacktestProgress], None] | None = None,
    ) -> BacktestResult:
        sessions = sorted(
            {
                bar.session_date
                for symbol, bars in bars_by_symbol.items()
                if symbol != config.benchmark_symbol
                for bar in bars
                if config.start <= bar.session_date <= config.end
            }
        )
        if len(sessions) < 2:
            raise ValueError("backtest requires at least two exchange sessions")
        indexed = {
            symbol: {bar.session_date: bar for bar in bars}
            for symbol, bars in bars_by_symbol.items()
        }
        fee_model = FeeModel(
            config.fixed_fee,
            config.per_share_fee,
            config.percentage_fee,
            config.minimum_fee,
        )
        cash = config.initial_capital
        positions: dict[str, _Position] = {}
        pending: dict[date, list[tuple[str, date]]] = {}
        trades: list[Trade] = []
        equity_curve: list[EquityPoint] = []
        position_points: list[PositionPoint] = []
        warnings: set[str] = set()
        invested_session_count = 0
        total_traded_notional = Decimal(0)
        dividends = cash_dividends or {}

        for session_index, session_date in enumerate(sessions):
            for symbol, position in positions.items():
                cash_per_share = dividends.get((symbol, session_date), Decimal(0))
                if cash_per_share:
                    payment = cash_per_share * position.shares
                    cash += payment
                    position.dividends += payment
            for symbol, position in tuple(positions.items()):
                if session_index < position.entry_index + config.holding_sessions:
                    continue
                bar = indexed.get(symbol, {}).get(session_date)
                if bar is None:
                    warnings.add(f"missing exit bar for {symbol} on {session_date}")
                    if config.missing_exit_policy == "fail":
                        raise ValueError(f"missing exit bar for {symbol} on {session_date}")
                    exit_price = Decimal(0)
                else:
                    exit_price = _execution_price(
                        bar, config.execution, buy=False, bps=config.slippage_bps
                    )
                exit_fee = fee_model.calculate(shares=position.shares, price=exit_price)
                proceeds = exit_price * position.shares - exit_fee
                cash += proceeds
                total_traded_notional += exit_price * position.shares
                trades.append(
                    Trade(
                        symbol=symbol,
                        signal_date=position.signal_date,
                        entry_date=position.entry_date,
                        exit_date=session_date,
                        entry_price=position.entry_price,
                        exit_price=exit_price,
                        shares=position.shares,
                        entry_fee=position.entry_fee,
                        exit_fee=exit_fee,
                        dividends=position.dividends,
                        net_pnl=(exit_price - position.entry_price) * position.shares
                        - position.entry_fee
                        - exit_fee
                        + position.dividends,
                    )
                )
                del positions[symbol]

            for symbol, signal_date in pending.pop(session_date, []):
                if symbol in positions:
                    continue
                bar = indexed.get(symbol, {}).get(session_date)
                if bar is None:
                    warnings.add(f"missing entry bar for {symbol} on {session_date}")
                    continue
                price = _execution_price(bar, config.execution, buy=True, bps=config.slippage_bps)
                budget = config.initial_capital * config.position_size_fraction
                shares = int(min(budget, cash) // price) if price else 0
                while shares > 0:
                    fee = fee_model.calculate(shares=shares, price=price)
                    if price * shares + fee <= cash:
                        break
                    shares -= 1
                if shares <= 0:
                    warnings.add(f"insufficient cash for {symbol} on {session_date}")
                    continue
                entry_fee = fee_model.calculate(shares=shares, price=price)
                cash -= price * shares + entry_fee
                total_traded_notional += price * shares
                positions[symbol] = _Position(
                    symbol,
                    signal_date,
                    session_date,
                    session_index,
                    price,
                    shares,
                    entry_fee,
                )

            market_value = sum(
                (
                    indexed[symbol][session_date].close * position.shares
                    for symbol, position in positions.items()
                    if session_date in indexed.get(symbol, {})
                ),
                Decimal(0),
            )
            if positions:
                invested_session_count += 1
            position_points.extend(
                PositionPoint(
                    session_date,
                    symbol,
                    position.shares,
                    indexed[symbol][session_date].close,
                    indexed[symbol][session_date].close * position.shares,
                )
                for symbol, position in positions.items()
                if session_date in indexed.get(symbol, {})
            )
            equity_curve.append(EquityPoint(session_date, cash + market_value, cash, market_value))

            snapshot = universe_snapshots.get(session_date)
            if snapshot is None:
                warnings.add(f"missing point-in-time universe snapshot on {session_date}")
                snapshot = frozenset(
                    symbol for symbol in bars_by_symbol if symbol != config.benchmark_symbol
                )
            available = {
                symbol: tuple(
                    bar
                    for bar in bars_by_symbol.get(symbol, ())
                    if bar.session_date <= session_date
                )
                for symbol in snapshot
            }
            view = PointInTimeDataView(
                available,
                cutoff_session=session_date,
                cutoff_at=datetime.combine(session_date, time(23, 59), UTC),
                universe_snapshot_id=f"{config.universe_version}:{session_date.isoformat()}",
            )
            selections = strategy.evaluate(view)
            if session_index + 1 < len(sessions):
                next_session = sessions[session_index + 1]
                scheduled = pending.setdefault(next_session, [])
                scheduled_symbols = {item[0] for item in scheduled}
                for selection in selections:
                    if (
                        selection.symbol not in positions
                        and selection.symbol not in scheduled_symbols
                    ):
                        scheduled.append((selection.symbol, session_date))
            if progress is not None:
                progress(
                    BacktestProgress(
                        session_date=session_date,
                        completed_sessions=session_index + 1,
                        total_sessions=len(sessions),
                        evaluation_segment=config.evaluation_segment,
                    )
                )

        final_date = sessions[-1]
        for symbol, position in tuple(positions.items()):
            bar = indexed.get(symbol, {}).get(final_date)
            if bar is None:
                warnings.add(f"open position {symbol} has no final liquidation bar")
                if config.missing_exit_policy == "fail":
                    raise ValueError(f"missing final liquidation bar for {symbol}")
                exit_price = Decimal(0)
            else:
                exit_price = _execution_price(
                    bar, config.execution, buy=False, bps=config.slippage_bps
                )
            exit_fee = fee_model.calculate(shares=position.shares, price=exit_price)
            trades.append(
                Trade(
                    symbol,
                    position.signal_date,
                    position.entry_date,
                    final_date,
                    position.entry_price,
                    exit_price,
                    position.shares,
                    position.entry_fee,
                    exit_fee,
                    position.dividends,
                    (exit_price - position.entry_price) * position.shares
                    - position.entry_fee
                    - exit_fee
                    + position.dividends,
                )
            )
            cash += exit_price * position.shares - exit_fee
            total_traded_notional += exit_price * position.shares
            del positions[symbol]
        if equity_curve:
            equity_curve[-1] = EquityPoint(final_date, cash, cash, Decimal(0))

        metrics = _metrics(
            equity_curve,
            trades,
            config,
            bars_by_symbol.get(config.benchmark_symbol, ()),
            invested_session_count,
            total_traded_notional,
        )
        manifest: dict[str, object] = {
            "strategy_id": strategy.strategy_id,
            "strategy_version": strategy.version,
            "strategy_parameters": dict(strategy.parameters),
            "configuration": config.model_dump(mode="json"),
            "warnings": sorted(warnings),
        }
        return BacktestResult(
            config,
            strategy.strategy_id,
            strategy.version,
            tuple(trades),
            tuple(position_points),
            tuple(equity_curve),
            metrics,
            tuple(sorted(warnings)),
            manifest,
        )


def _execution_price(
    bar: SignalBar, convention: ExecutionConvention, *, buy: bool, bps: Decimal
) -> Decimal:
    base = bar.open if convention is ExecutionConvention.NEXT_OPEN else bar.close
    adjustment = bps / Decimal(10_000)
    return base * (1 + adjustment if buy else 1 - adjustment)


def _metrics(
    curve: Sequence[EquityPoint],
    trades: Sequence[Trade],
    config: BacktestConfig,
    benchmark: Sequence[SignalBar],
    invested_sessions: int,
    traded_notional: Decimal,
) -> BacktestMetrics:
    ending = curve[-1].equity
    total_return = ending / config.initial_capital - 1
    periods = max(len(curve) - 1, 1)
    annualized_return = Decimal(
        str((float(ending / config.initial_capital) ** (252 / periods)) - 1)
    )
    returns = [
        float(curve[index].equity / curve[index - 1].equity - 1)
        for index in range(1, len(curve))
        if curve[index - 1].equity
    ]
    mean_return = sum(returns) / len(returns) if returns else 0.0
    variance = (
        sum((value - mean_return) ** 2 for value in returns) / max(len(returns) - 1, 1)
        if returns
        else 0.0
    )
    daily_volatility = math.sqrt(variance)
    annualized_volatility = Decimal(str(daily_volatility * math.sqrt(252)))
    daily_risk_free = float(config.annual_risk_free_rate) / 252
    sharpe = (
        (mean_return - daily_risk_free) / daily_volatility * math.sqrt(252)
        if daily_volatility
        else 0.0
    )
    downside = [min(value - daily_risk_free, 0.0) for value in returns]
    downside_deviation = (
        math.sqrt(sum(value * value for value in downside) / len(downside)) if downside else 0
    )
    sortino = (
        (mean_return - daily_risk_free) / downside_deviation * math.sqrt(252)
        if downside_deviation
        else 0.0
    )
    peak = curve[0].equity
    maximum_drawdown = Decimal(0)
    for point in curve:
        peak = max(peak, point.equity)
        drawdown = point.equity / peak - 1 if peak else Decimal(0)
        maximum_drawdown = min(maximum_drawdown, drawdown)
    calmar = annualized_return / abs(maximum_drawdown) if maximum_drawdown else Decimal(0)
    hit_rate = (
        Decimal(sum(trade.net_pnl > 0 for trade in trades)) / Decimal(len(trades))
        if trades
        else Decimal(0)
    )
    average_holding = (
        Decimal(sum((trade.exit_date - trade.entry_date).days for trade in trades))
        / Decimal(len(trades))
        if trades
        else Decimal(0)
    )
    benchmark_values = [
        bar.total_return_close if bar.total_return_close is not None else bar.close
        for bar in benchmark
        if config.start <= bar.session_date <= config.end
    ]
    benchmark_return = (
        benchmark_values[-1] / benchmark_values[0] - 1
        if len(benchmark_values) >= 2 and benchmark_values[0]
        else Decimal(0)
    )
    average_equity = sum((point.equity for point in curve), Decimal(0)) / Decimal(len(curve))
    return BacktestMetrics(
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        sharpe_ratio=Decimal(str(sharpe)),
        sortino_ratio=Decimal(str(sortino)),
        maximum_drawdown=maximum_drawdown,
        calmar_ratio=calmar,
        hit_rate=hit_rate,
        exposure=Decimal(invested_sessions) / Decimal(len(curve)),
        turnover=traded_notional / average_equity if average_equity else Decimal(0),
        trade_count=len(trades),
        average_holding_period=average_holding,
        benchmark_return=benchmark_return,
        benchmark_relative_return=total_return - benchmark_return,
    )
