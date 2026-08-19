# Closing Signal

Closing Signal is a local, centrally operated end-of-day screening and notification
service for NYSE- and Nasdaq-listed common stocks, ETFs, and ADRs. It uses Alpaca
for market data, official SEC EDGAR sources for offering alerts, SMTP email for
delivery, and a point-in-time backtest engine that reuses the production strategy
interface.

The subscriber-facing end-of-day digest is branded **The Closing Signal**.

The U.S. product replaces the former China A-share runtime completely. It does
not place orders or redistribute raw market data.

The approved product contract and external commercial-launch gates are in the
[U.S. Equities PRD](docs/PRD_US_EQUITIES.md). Implementation status and known
gaps are tracked in [PRD implementation status](docs/PRD_IMPLEMENTATION_STATUS.md).
See the [local operations guide](docs/OPERATIONS.md) for scheduling and recovery.
Use the [manual test plan](docs/MANUAL_TEST_PLAN.md) for release rehearsals,
provider acceptance, controlled delivery, recovery, and commercial-gate evidence.
The [missing-strategy research](docs/MISSING_STRATEGIES_RESEARCH.md) prioritizes
the next evidence-backed strategy families and records their data requirements.

## Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- Alpaca paper credentials and IEX for development; written SIP rights and a SIP
  entitlement are required for commercial production
- OpenFIGI credentials; Nasdaq and SEC sources reconcile reference classifications
- A Brevo SMTP account if notifications are enabled
- A public SEC organization/contact identity
- A real sender domain with SPF, DKIM, and DMARC before live subscriber delivery

## Install

```powershell
uv sync --locked
Copy-Item .env.example .env
Copy-Item config/settings.example.toml config/settings.toml
Copy-Item config/strategies.example.json config/strategies.json
Copy-Item config/sec-rules.example.json config/sec-rules.json
Copy-Item config/subscribers.example.json config/subscribers.json
```

Secrets belong only in `.env` or the process environment. Do not put them in
TOML/JSON configuration, the database, reports, or subscriber files.

Replace the two `your-domain.example` values in `config/settings.toml`, copy the
`*.example.json` files to their runtime names, and set the documented secrets.
The examples are the approved development/private-beta baseline; they cannot
authorize a commercial SIP feed or invent your identity. Then validate everything:

```powershell
uv run closing-signal --config config/settings.toml validate-config
```

## Operator workflow

```powershell
# Build the eligible catalog and dated universe snapshot.
uv run closing-signal --config config/settings.toml sync-universe

# Backfill raw, split-adjusted, and all-adjusted daily series from 2016.
uv run closing-signal --config config/settings.toml backfill --start 2016-01-01

# Normal completed-session workflow.
uv run closing-signal --config config/settings.toml sync-daily
uv run closing-signal --config config/settings.toml screen --dry-run
uv run closing-signal --config config/settings.toml screen
uv run closing-signal --config config/settings.toml sec-sync

# Operations and recovery.
uv run closing-signal --config config/settings.toml retry-notifications
uv run closing-signal --config config/settings.toml health
uv run closing-signal --config config/settings.toml data-audit

# A backtest request supplies every economic and reproducibility assumption.
uv run closing-signal --config config/settings.toml backtest --request config/backtest.example.json
```

Commands return zero only for their defined success state. Mutating commands use
an atomic local lock. `screen --reprocess` recalculates a session without
automatically resending successful notifications; `retry-notifications` is the
explicit resend path and preserves recipient idempotency.

`health` is an active dependency check. It returns nonzero for corrupt SQLite
state, insufficient disk, stale market data, unavailable or unauthorized Alpaca
or SMTP access, or missing, failed, running, or stale scheduled operations. Lag,
age, and disk thresholds are mandatory configuration rather than embedded
production defaults.

## Strategies

Six U.S.-market strategies are implemented:

- moving-average crossover with volume confirmation;
- high tight flag;
- Turtle channel breakout ranked by an explicitly configured basis;
- cross-sectional Relative Price Strength breakout;
- gap-up shakeout using configurable U.S. gap bounds;
- uptrend shock-reversal using ATR, volume, and close-location evidence.

Every threshold and lookback is required in the versioned strategy file. The
included `us-research-v1` values are experimental baselines, not performance claims.

## Data and correctness model

- Alpaca catalog, daily bars, calendar, and current corporate-actions endpoint
- official session dates, early closes, New York DST, and a configured
  post-close finalization delay
- stable instrument identity separate from ticker symbols
- raw, split-adjusted, and all-adjusted series stored under distinct keys
- idempotent ingestion, page/chunk resume state, bounded exponential retry, and
  quarantine records for missing or suspect data
- dated universe snapshots for survivorship-bias disclosure and backtesting
- SEC accession-number deduplication, evidence-bearing classification, and no
  confidence-based alert suppression
- SEC historical-submission shard traversal bounded at `sec_history_start`

## Backtest artifacts

Each run writes:

- `manifest.json`
- `trades.csv`
- `positions.csv`
- `equity_curve.csv`
- `metrics.json`
- `warnings.json`
- `failures.json`
- `report.md`

A walk-forward run instead writes `experiment-manifest.json`,
`experiment-report.md`, and complete segment bundles beneath each `fold-NNN`
directory. Every candidate gets isolated `training` and `validation` bundles;
only the selected candidate gets an `out-of-sample` bundle.

The request explicitly names its in-sample, validation, or out-of-sample segment.
Anchored and rolling window construction plus metric-driven candidate selection
are available in `closing_signal.backtest`; the selector receives only training and
validation results, and the chosen candidate is evaluated on the test segment
after selection. Use `config/backtest-walk-forward.example.json` as the strict
operator request template.

Long backtests emit bounded JSON progress every 25 simulated sessions and at
each segment boundary. Interrupting the command before completion writes no
result bundle; the operation remains recorded as interrupted/running for health
diagnosis while the global process lock is released.

## Development

```powershell
uv lock --check
uv run black --check .
uv run ruff check .
uv run mypy
uv run pytest --cov=closing_signal --cov-report=term-missing --cov-fail-under=80
uv run pip-audit
uv build
```

Black is the formatter, Ruff handles linting/imports, mypy runs in strict mode,
and pytest includes unit, contract, property, integration-boundary, and
reproducibility tests.

## License

MIT
