# CLI Progress and Universe Sync Diagnostics Design

## Problem

Long-running operator commands can remain silent for minutes, making healthy
work indistinguishable from a hung process. `sync-universe` is especially opaque
because it fetches and classifies tens of thousands of Alpaca assets through
multiple reference providers before printing anything.

The observed universe sync also rejected the entire eligible catalog and printed
only aggregate counts:

```text
{"accepted": 0, "rejected": 33441, "session": "2026-08-19", "status": "failed", "warnings": 0}
```

Investigation showed two rejection categories in the local quarantine database:
assets outside the supported NYSE/Nasdaq venues and OpenFIGI mapping failures,
including AAPL. Their accumulated occurrence counts matched all 33,441 reported
rejections. A live single-symbol request reproduced the root cause: OpenFIGI now
rejects `ID_EXCH_SYMBOL` jobs without a `securityType2` field. The current client
discards that provider error and emits the generic reason
`OpenFIGI did not return a mapping`. A `TICKER` request with `exchCode: US`
successfully maps AAPL without requiring the security type that the application
is trying to discover.

## Goals

- Give immediate and periodic, human-readable feedback for long-running CLI
  commands.
- Keep stdout suitable for automation by reserving it for the final JSON result.
- Fix OpenFIGI universe classification without weakening the existing
  Nasdaq/SEC reconciliation rules.
- Make universe-sync failures actionable with bounded reason counts, examples,
  and a suggested next step.
- Keep credentials and full provider payloads out of terminal output and logs.

## Non-goals

- Add a graphical terminal UI, spinner, or third-party progress dependency.
- Change strategy logic, universe eligibility rules, or notification semantics.
- Emit a line for every asset, bar, filing, or backtest session.
- Change final command exit-code semantics.

## Output contract

Progress is plain text written to stderr and flushed immediately. Every command
emits an initial line so a slow first network request is visible:

```text
[sync-universe] Starting
[sync-universe] Fetching Alpaca asset catalog
[sync-universe] Classifying assets with OpenFIGI: batch 10/106 (1,000/10,527)
[sync-universe] Reconciling Nasdaq and SEC references
[sync-universe] Persisting accepted instruments and quarantine findings
```

Progress messages contain operation, stage, and bounded counts when a total is
known. They never contain credentials, subscriber addresses, raw response
bodies, or unbounded symbol lists. Non-interactive callers can suppress progress
by redirecting stderr without affecting the final result.

Stdout contains only the final JSON summary. Existing summary fields remain
available. A failed universe sync adds a bounded `rejection_reasons` array and a
`next_step` string:

```json
{
  "accepted": 0,
  "next_step": "Review rejection_reasons and provider credentials, then rerun sync-universe.",
  "rejected": 33441,
  "rejection_reasons": [
    {
      "count": 22763,
      "examples": ["SGGKY", "LVCC", "TVNB"],
      "reason": "venue is not NYSE or Nasdaq"
    },
    {
      "count": 10678,
      "examples": ["AAPL", "MSFT", "NVDA"],
      "reason": "OpenFIGI API error: securityType2 required with ID_EXCH_SYMBOL(idType)."
    }
  ],
  "session": "2026-08-19",
  "status": "failed",
  "warnings": 0
}
```

Reason diagnostics are computed from the current fetch result rather than the
historical quarantine table. They include at most five reasons and three example
symbols per reason, ordered by descending count and then reason text for stable
output.

## Progress architecture

Add a small progress module containing an immutable progress event and a
reporter callback protocol. The default CLI reporter formats events as
`[operation] message` on stderr with `flush=True`. A no-op callback remains the
default below the CLI boundary so providers and services can be reused in tests
or libraries without producing output.

Operations pass the callback into long-running providers and services. Progress
events cross existing boundaries instead of allowing low-level components to
print directly. This keeps formatting and output routing centralized while
allowing the component that knows the batch or item count to report it.

The CLI dispatch emits the immediate `Starting` event. Operation-specific events
cover these milestones:

| Command | Progress milestones |
| --- | --- |
| `sync-universe` | Alpaca catalog request, OpenFIGI batches, Nasdaq/SEC reconciliation, persistence |
| `backfill` | calendar/range selection, each adjustment mode, ingestion chunks, corporate-action chunks |
| `sync-daily` | calendar/session selection, each adjustment mode, ingestion chunks, corporate-action chunks |
| `screen` | data preparation and each enabled strategy; `retry-notifications` inherits these events |
| `sec-sync` | reference loading and every bounded issuer interval, plus filing/delivery totals |
| `data-audit` | incomplete-series scan, factor-consistency scan, quarantine persistence |
| `backtest` | request/data preparation and the existing bounded session/segment milestones |

`health` also receives the immediate CLI start line. Its individual probes are
short and already appear in the final structured result, so no additional
fine-grained events are required.

Batch-based loops report their first unit, last unit, and bounded intermediate
intervals. The interval is chosen per operation to avoid excessive scheduler
logs: OpenFIGI reports every ten batches, market ingestion reports each existing
provider chunk, SEC synchronization reports every 100 eligible issuers, and
backtests retain the current 25-session interval. Final summaries are not
duplicated on stderr.

## OpenFIGI correction

Change mapping jobs from:

```json
{"idType": "ID_EXCH_SYMBOL", "idValue": "AAPL", "micCode": "XNAS", "marketSecDes": "Equity"}
```

to:

```json
{"idType": "TICKER", "idValue": "AAPL", "exchCode": "US", "marketSecDes": "Equity"}
```

The application continues to accept a classification only when the returned
OpenFIGI records resolve to exactly one supported instrument type. The existing
Nasdaq ETF flag and Nasdaq/SEC venue checks remain authoritative conflict gates.

OpenFIGI response parsing preserves a provider `error` or `warning` string as a
sanitized issue reason when no data is returned. This exposes contract changes
and invalid requests without including request headers or full payloads. Unknown
response shapes retain explicit local validation messages.

## Error handling

- HTTP and retry failures continue through the existing bounded retry policy.
- Provider errors that apply to individual mapping jobs become per-symbol
  rejection reasons and appear in the bounded final diagnostic summary.
- Structural batch-response errors still fail the command because results can no
  longer be safely aligned to requested symbols.
- Progress-reporting failures must not mask the operation result; the default
  reporter performs only a guarded stderr write.
- A zero-accepted universe remains a failed command with its existing nonzero
  exit code.

## Testing

Tests will be written before implementation and will cover:

1. OpenFIGI jobs use `TICKER`, `exchCode: US`, and no obsolete MIC constraint.
2. A successful AAPL-style response produces a common-stock classification.
3. OpenFIGI `error` and `warning` fields survive as actionable issue reasons.
4. Universe-sync failure JSON aggregates current rejection reasons with stable,
   bounded example symbols and a next step.
5. Progress is written to stderr, final JSON is written to stdout, and both
   streams flush at the correct boundary.
6. Each long-running operation emits an immediate event and its meaningful
   bounded milestones without leaking secrets or per-record noise.
7. Existing CLI, provider, operation, ingestion, SEC, and backtest tests remain
   green, followed by the complete project test suite and quality checks.

## Documentation

Update the README operator workflow and operations guide to state that progress
uses stderr and final summaries use stdout. Document redirection for automation
and the new actionable `sync-universe` rejection fields.
