# Closing Signal

Closing Signal is a local, centrally operated end-of-day screening and notification
service for NYSE- and Nasdaq-listed common stocks, ETFs, and ADRs. It uses Alpaca
for market data, official SEC EDGAR sources for offering alerts, SMTP email for
delivery, and a point-in-time backtest engine that reuses the production strategy
interface.

The subscriber-facing end-of-day digest is branded **The Closing Signal**. It does
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
cp .env.example .env
cp config/settings.example.toml config/settings.toml
cp config/strategies.example.json config/strategies.json
cp config/sec-rules.example.json config/sec-rules.json
cp config/subscribers.example.json config/subscribers.json
```

Secrets belong only in `.env` or the process environment. Do not put them in
TOML/JSON configuration, the database, reports, or subscriber files.

### Create provider credentials

Create the required provider accounts and copy their credentials into `.env`:

1. **Alpaca:** [create a free paper-trading account](https://app.alpaca.markets/signup),
   select the **Paper Trading** account in the dashboard, and follow Alpaca's
   [paper API key instructions](https://alpaca.markets/learn/start-paper-trading#step-1-fetch-your-alpacas-trading-api-keys).
   Set `CLOSING_SIGNAL_ALPACA_API_KEY` to the paper API key and
   `CLOSING_SIGNAL_ALPACA_API_SECRET` to its secret. Save the secret when it is
   displayed; generating a replacement invalidates the current key pair.
2. **OpenFIGI:** [create an OpenFIGI account](https://www.openfigi.com/user/signup),
   then create or view the key on your account page as described in the
   [OpenFIGI API documentation](https://www.openfigi.com/api/documentation).
   Set `CLOSING_SIGNAL_OPENFIGI_API_KEY` to that key.
3. **SEC EDGAR:** no SEC account or API key is required. Choose a public
   organization name and monitored contact email, following the SEC's
   [declared user-agent guidance](https://www.sec.gov/about/webmaster-frequently-asked-questions#developers).
   Set them as `CLOSING_SIGNAL_SEC_ORGANIZATION` and
   `CLOSING_SIGNAL_SEC_CONTACT_EMAIL`.
4. **Brevo** (when notifications are enabled):
   [create a Brevo account](https://onboarding.brevo.com/account/register), then
   follow the guide to [create an SMTP key](https://help.brevo.com/hc/en-us/articles/7959631848850-Create-and-manage-your-SMTP-keys).
   Set `CLOSING_SIGNAL_SMTP_USERNAME` to the SMTP login shown on Brevo's
   **SMTP & API** page and `CLOSING_SIGNAL_SMTP_PASSWORD` to the SMTP key—not a
   Brevo API key. Copy the SMTP key when it is displayed because Brevo will not
   show the full value again. Before live delivery, also
   [authenticate the sender domain](https://help.brevo.com/hc/en-us/articles/12163873383186-Authenticate-your-domain-with-Brevo-Brevo-code-DKIM-DMARC).

Replace the two `your-domain.example` values in `config/settings.toml`, copy the
`*.example.json` files to their runtime names, and set the documented secrets.
The examples are the approved development/private-beta baseline; they cannot
authorize a commercial SIP feed or invent your identity. Then validate everything:

```powershell
uv run closing-signal --config config/settings.toml validate-config
```

## Operator workflow

Commands that accept a session date default to the latest completed exchange
session. Run the completed-session sequence only after the configured
post-close finalization delay. Mutating commands acquire a single local lock, so
run them sequentially rather than from overlapping shells or scheduler jobs.

### First-time data preparation

```powershell
uv run closing-signal --config config/settings.toml sync-universe
uv run closing-signal --config config/settings.toml backfill --start 2016-01-01
```

- `sync-universe` fetches Alpaca's asset catalog, reconciles eligible NYSE and
  Nasdaq instruments against the configured reference sources, stores stable
  instrument identities, and writes a dated universe snapshot. Rejected or
  questionable records are quarantined for review. This must run first because
  market-data ingestion and screening need both the instrument IDs and the
  point-in-time membership snapshot. Run it again at the start of each nightly
  workflow so new listings and classification changes are captured.
- `backfill` downloads every completed session from the requested start date
  through the latest completed session. It stores separate raw, split-adjusted,
  and all-adjusted daily series plus corporate actions, using resumable chunks.
  This creates the history required by strategy lookbacks and backtests; rerun
  the same command after a partial result to resume incomplete chunks.

### Completed-session workflow

After the official close plus the configured finalization delay, run these in
order:

```powershell
uv run closing-signal --config config/settings.toml sync-universe
uv run closing-signal --config config/settings.toml sync-daily
uv run closing-signal --config config/settings.toml data-audit
uv run closing-signal --config config/settings.toml screen
uv run closing-signal --config config/settings.toml sec-sync
uv run closing-signal --config config/settings.toml health
```

- `sync-universe` refreshes the eligible catalog and records membership for the
  session before any prices or signals are processed. This prevents the screen
  from using a stale list of securities.
- `sync-daily` fetches the configured recent window of completed daily bars in
  all three adjustment modes and refreshes recent corporate actions. The overlap
  intentionally captures provider corrections, late bars, splits, and dividends
  before strategies run.
- `data-audit` checks that each stored daily bar has raw, split-adjusted, and
  all-adjusted versions and that split factors are consistent across OHLC
  prices. It quarantines new findings so incomplete or internally inconsistent
  data can be investigated before relying on the screen or a backtest.
- `screen` evaluates every enabled strategy against split-adjusted history and
  the session's saved universe, persists the results, and sends each category to
  its eligible subscribers. Existing strategy run keys are skipped, making the
  normal command safe to rerun without duplicating a completed digest.
- `sec-sync` maps eligible common stocks and ADRs to SEC issuers, discovers the
  configured filing forms, classifies their offering evidence, and sends alerts
  to matching subscribers. Processed accession numbers are stored so the same
  filing is not alerted twice. Schedule this independently at the higher cadence
  described in the operations guide when timely SEC alerts are required.
- `health` verifies SQLite integrity, free disk space, Alpaca connectivity and
  market-data freshness, SMTP connectivity, and the status and age of required
  scheduled operations. Run it last so its nonzero exit status can alert the
  scheduler when the completed-session workflow or a dependency is unhealthy.

### Safe previews and recovery

```powershell
# Evaluate and render without saving strategy results or sending email.
uv run closing-signal --config config/settings.toml screen --dry-run

# Discover and classify SEC filings without sending or marking them processed.
uv run closing-signal --config config/settings.toml sec-sync --dry-run

# Retry only recipients that do not already have a successful delivery key.
uv run closing-signal --config config/settings.toml retry-notifications

# Recalculate a session without automatically resending successful notifications.
uv run closing-signal --config config/settings.toml screen --reprocess
```

- Use `screen --dry-run` during initial setup and after changing strategies,
  subscribers, templates, or sender settings. It exercises strategy evaluation,
  recipient selection, and rendering without persisting a strategy result or
  contacting recipients.
- Use `sec-sync --dry-run` to inspect SEC discovery, classification, recipient
  routing, and rendered alerts without sending messages or saving accession
  numbers as processed.
- Use `retry-notifications` after an SMTP or recipient-specific failure. It
  re-evaluates the requested session but preserves recipient idempotency, so
  successful deliveries are not sent again.
- Use `screen --reprocess` when corrected data or configuration requires a fresh
  calculation for an existing session. Reprocessing updates the calculation but
  defaults delivery to a dry run; notification retries remain an explicit,
  separate action.

### Historical evaluation

```powershell
uv run closing-signal --config config/settings.toml backtest --request config/backtest.example.json
```

`backtest` validates the request and configuration versions, loads the requested
strategy and stored point-in-time data, and runs either a single historical
segment or a walk-forward experiment. The request supplies the economic,
selection, and reproducibility assumptions; the resulting artifact bundle is
the evidence used to compare strategies without changing or sending the live
screen. Run `sync-universe`, `backfill`, and `data-audit` first so the evaluation
has complete history and dated universe snapshots.

Every command prints a JSON summary. A zero exit status means that command
reached its defined success state; partial, failed, and unhealthy results return
nonzero so a shell or scheduler can detect them.

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
