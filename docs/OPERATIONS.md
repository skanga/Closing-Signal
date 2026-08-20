# Closing Signal local operations guide

This guide implements the approved local development/private-beta baseline.
Commercial production is not authorized until the external gates in PRD Section
18 are evidenced, especially written SIP rights and legal/privacy review.

## Configuration inventory

Keep these operator-managed files readable only by the service account where the
operating system permits it:

- `.env`: Alpaca, OpenFIGI, SEC identity, and Brevo SMTP credentials; never commit it.
- `settings.toml` or `settings.json`: non-secret runtime choices.
- OpenFIGI primary reference data, reconciled at runtime against Nasdaq and SEC
  sources. `asset-types.json` is an explicit offline/test fallback only.
- `strategies.json`: versioned enabled strategies and every parameter.
- `sec-rules.json`: versioned offering classifications and evidence phrases.
- `sec_history_start`: earliest filing date to traverse through SEC historical
  submission shards; the approved MVP value is `2016-01-01`.
- `subscribers.json`: normalized addresses, categories, double-opt-in evidence,
  policy version, and activation/deactivation evidence.
- a single-segment or walk-forward backtest request JSON. Walk-forward requests
  must provide at least two complete parameter sets and an explicit selection
  metric and direction.

Run `validate-config` after every configuration change. Success writes
`{"message":"configuration valid","status":"complete"}` to stdout. Validation
failures write one JSON object with `status: "failed"` and an `error` message;
human-readable detail remains on stderr without printing secret values.

The baseline health thresholds are one market session, 26 hours for required
operations, and 10 GiB free disk. The required schedule inventory contains
`sync-daily`, `screen`, and `sec-sync`.

## First-time sequence

```powershell
uv sync --locked
uv run closing-signal --config config/settings.toml validate-config
uv run closing-signal --config config/settings.toml sync-universe
uv run closing-signal --config config/settings.toml backfill --start 2016-01-01
uv run closing-signal --config config/settings.toml data-audit
uv run closing-signal --config config/settings.toml screen --dry-run
uv run closing-signal --config config/settings.toml sec-sync --dry-run
uv run closing-signal --config config/settings.toml backtest --request config/backtest.json
```

Do not enable scheduled email until the dry-run output, subscribers, source
links, strategy parameters, SEC rules, and sender domain have been reviewed.

## Completed-session sequence

Run these in order after the official close plus 150 minutes. Normal full-session
targets are 18:30 ET sync, 18:45 ET screen, and 19:00 ET digest; derive early-close
targets from the actual calendar close rather than hard-coding wall-clock times:

```powershell
uv run closing-signal --config config/settings.toml sync-universe
uv run closing-signal --config config/settings.toml sync-daily
uv run closing-signal --config config/settings.toml data-audit
uv run closing-signal --config config/settings.toml screen
uv run closing-signal --config config/settings.toml sec-sync
uv run closing-signal --config config/settings.toml health
```

Progress lines are written to stderr and flushed as work advances. Every terminal
path writes exactly one JSON object to stdout only after required bookkeeping
succeeds; failures use `status: "failed"` and an `error` message. Capture both
streams in scheduled jobs. A lack of new progress beyond the normal provider
timeout/retry window is actionable, as is any nonzero final exit status.
Universe-sync failures include bounded current-run reason counts and sample
symbols; investigate those fields before rerunning.

The application rejects a daily or screening date later than the latest
completed exchange session. A single database lock prevents overlapping mutating
commands.

Every dispatched mutating command is recorded as `running` before execution and
then finalized as `complete` or `failed`. The health command fails when a
required operation never ran, failed, remains running after termination, or is
older than the configured maximum age. It also actively probes Alpaca and SMTP;
run it only where outbound connectivity is expected.

Backtests print progress every 25 simulated sessions. To cancel, interrupt the
process once and wait for it to exit; artifacts are emitted only after the full
single run or walk-forward experiment completes. A cancelled run therefore
cannot be mistaken for a complete report.

## Scheduling

Production uses a dedicated Ubuntu 24.04.4 LTS mini-PC, encrypted disk, UPS,
wired networking, a dedicated service account, and ordered `systemd` oneshot
units/timers. Windows Task Scheduler remains a supported development option.

- Nightly: universe sync, exact five-session bar correction, exact 63-session
  corporate-action correction, audit, screen, digest, and health.
- SEC: every ten minutes 06:00–22:00 ET weekdays and hourly otherwise.
- Weekly: fresh 252-session market-data reconciliation.
- Quarterly: full data audit and restoration drill.

IEX is permitted only for development and contract tests. The production unit
must refuse rollout until configuration selects `sip` and the operator records
the corresponding written commercial rights.

## Email and consent

Brevo Free uses `smtp-relay.brevo.com:587` with STARTTLS. Keep the 300-provider-
attempt daily application cap and a maximum of 25 initial beta subscribers.
Retries total four attempts with delays of 60, 300, and 1,800 seconds. Upgrade
the plan when usage reaches 80% of the daily allowance on seven of 30 days.

Do not activate a subscriber without timezone-aware double-opt-in evidence and
the accepted policy version. For the monitored private beta, publish a support
address and process unsubscribe requests within one business day. Public launch
requires hosted one-click unsubscribe and synchronized provider suppression.
Configure SPF, DKIM, and DMARC before any live delivery.

## Recovery

- `partial` market sync: rerun the identical command. Completed symbol chunks are
  skipped; only failed chunks are requested again.
- failed email recipients: run `retry-notifications`. Successful recipient keys
  are skipped.
- strategy recalculation without resend: run `screen --reprocess`.
- inspection without mutation: run `health` and then `data-audit`.
- stale lock after a killed process: verify no Closing Signal process is active, back
  up the database, and remove only the `mutating-operation` row from
  `operation_locks` using an approved SQLite administration tool.

Never delete ingestion, delivery, SEC accession, universe-snapshot, or subscriber
audit rows merely to force a rerun.

## Backup and migration

Take a consistent encrypted SQLite backup nightly while no mutating command is
running, with one encrypted local copy and one encrypted offsite copy. Retain 7
daily, 5 weekly, and 12 monthly copies. The targets are RPO 24 hours and RTO four
hours; restore to a separate path and verify configuration/manifests quarterly.
The operator must name and test the offsite target before commercial production.

Retain reproducibility manifests seven years, delivery audit 13 months,
operational logs 90 days, subscriber PII while active plus 30 days, consent and
unsubscribe evidence seven years, lawful suppression evidence indefinitely, and
raw provider response bodies no longer than 45 days. Market-data retention must
follow the governing provider contract.
