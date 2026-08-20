# Closing Signal manual test plan

| Field | Value |
|---|---|
| Product | Closing Signal |
| Plan version | 1.0 |
| Date | 2026-08-19 |
| Test target | Development and controlled private-beta release candidate |
| Default environment | Alpaca paper API with IEX, OpenFIGI, Nasdaq/SEC reconciliation, Brevo test recipients |
| Production status | Not authorized until the external gates in the PRD are satisfied |

## 1. Purpose

This plan validates the complete operator journey on real local infrastructure:
installation, configuration, reference data, market data, screening, SEC filing
monitoring, email delivery, subscriber consent, backtesting, recovery, security,
backup, and operational health. It complements the automated suite; it does not
replace unit, contract, integration-boundary, or coverage gates.

Manual testing must use a dedicated database, controlled email addresses, Alpaca
paper credentials, and non-production subscriber data. Never run destructive or
failure-injection cases against the canonical beta database.

## 2. Scope

### In scope

- Windows development installation and the Ubuntu 24.04.4 LTS target host.
- Python/uv packaging and CLI behavior.
- OpenFIGI primary classification with Nasdaq and SEC reconciliation.
- Alpaca paper/IEX catalog, daily bars, calendars, and corporate actions.
- The six `us-research-v1` strategies.
- SEC EDGAR discovery, classification, uncertainty, and deduplication.
- Brevo SMTP delivery to controlled recipients.
- Subscriber consent, routing, suppression, and audit evidence.
- Single and walk-forward backtests and their exported artifacts.
- Restart, retry, locking, degraded dependency, health, backup, and restore paths.
- Privacy/security checks observable by a local operator.

### Out of scope for this test cycle

- Order entry, brokerage, portfolio management, or investment advice.
- Intraday or real-time screening.
- WhatsApp delivery, billing, web administration, and customer self-service.
- Load testing that sends unsolicited email or exceeds provider terms.
- Legal conclusions or verification of contractual rights by engineering.

### Known implementation gaps

The following cases should remain `Blocked` or `Expected fail` until their
implementation lands; they are included so they cannot be lost at release time:

- one combined technical daily digest;
- direct SEC alerts separated from a daily digest for all other candidates;
- hosted one-click unsubscribe and synchronized Brevo suppression;
- automated weekly 252-session reconciliation;
- production systemd unit/timer files;
- automated encrypted local/offsite backup, pruning, and restore verification.

## 3. Roles

| Role | Responsibility |
|---|---|
| Test lead | Owns scope, environment, evidence, defects, and final report. |
| Operator | Executes CLI and host-level cases using the dedicated service account. |
| Data reviewer | Reviews classifications, bars, corporate actions, and filing evidence. |
| Email reviewer | Controls test recipients and verifies rendering, authentication, and suppression. |
| Product owner | Accepts strategy presentation, digest content, and documented limitations. |
| Release approver | Confirms all P0 cases and external gates before commercial production. |

One person may perform multiple roles in a private beta, but the executor and
approver should be recorded for every release-blocking case.

## 4. Entry criteria

Testing begins only when:

1. `uv lock --check`, Black, Ruff, strict mypy, pytest with at least 80% combined
   branch-aware coverage, dependency audit, and `uv build` pass.
2. The release candidate is identified by an immutable commit or package hash.
3. A dedicated test directory and database path such as `data/manual-test.db`
   have been selected.
4. Alpaca paper, OpenFIGI, SEC identity, and Brevo test credentials are available
   through the process environment or a protected `.env`.
5. Only controlled recipient addresses are present in the test subscriber file.
6. The current provider terms and rate limits have been reviewed.
7. A copy of any pre-existing test database has been backed up before recovery or
   corruption exercises.

## 5. Exit criteria

The development/private-beta candidate passes when:

- every P0 case applicable to that release is `Pass`;
- no open Severity 1 or Severity 2 defect remains;
- expected failures match the known-gap list and have an owner/milestone;
- no real subscriber received an unintended message;
- no secret or unnecessary subscriber data appears in logs or artifacts;
- data samples and notification evidence have been independently reviewed;
- restore and recovery evidence meet the environment's declared scope.

Commercial production additionally requires every external gate in PRD Section
18: written SIP rights, real identities/domain, SPF/DKIM/DMARC, hosted one-click
unsubscribe, credentialed provider acceptance, counsel approval, and a tested
encrypted offsite backup target.

## 6. Environment and test data

### Environment matrix

| Environment | Purpose | Required coverage |
|---|---|---|
| Windows 11, supported Python, uv | Developer/operator smoke test | Installation, configuration, CLI, data, strategies, backtests, controlled email |
| Ubuntu 24.04.4 LTS mini-PC | Production-target rehearsal | Full P0 suite, permissions, restart, disk, backup/restore, scheduler when available |
| Provider-disabled test profile | Negative-path testing | Missing/invalid credentials, network outage, timeouts, retry behavior |

Record OS build, CPU, RAM, free disk, Python version, uv version, application
version, package hash, timezone, and test start/end time.

### Controlled reference sample

Use the current provider catalog to select at least:

- one Nasdaq common stock;
- one NYSE common stock;
- one ETF from each supported venue where available;
- one ADR;
- one punctuation/share-class symbol;
- one recently listed or renamed security if available;
- one explicit non-eligible venue/type as a rejection sample;
- one OpenFIGI/Nasdaq/SEC conflict or deliberately altered fixture for quarantine.

Record the symbols and source retrieval timestamps. Symbols are selected at test
time because listings and classifications change; do not hard-code a security as
permanently representative.

### Evidence package

Create a folder named with the release ID and test date. Store:

- a redacted environment inventory;
- command, timestamp, exit code, and captured output for every case;
- configuration files with secrets removed;
- database/report hashes before and after relevant cases;
- screenshots or raw message sources for controlled emails;
- source URLs and timestamps for manual data/filing comparisons;
- defect IDs, retest results, and approvals.

Never place `.env`, credentials, live subscriber lists, or full provider response
bodies into the evidence package.

## 7. Common setup

From a clean checkout or unpacked release candidate:

```powershell
uv sync --locked
Copy-Item .env.example .env
Copy-Item config/settings.example.toml config/manual-test.toml
Copy-Item config/strategies.example.json config/strategies.json
Copy-Item config/sec-rules.example.json config/sec-rules.json
Copy-Item config/subscribers.example.json config/manual-subscribers.json
```

In `config/manual-test.toml`:

- set `database_path = "data/manual-test.db"`;
- set `subscriber_file = "config/manual-subscribers.json"`;
- replace both `your-domain.example` values;
- retain the Alpaca paper endpoint and `alpaca_feed = "iex"`;
- do not add secret values to TOML or JSON.

Replace the example subscriber with controlled addresses and real double-opt-in
timestamps. Then run:

```powershell
uv run closing-signal --config config/manual-test.toml validate-config
```

Unless a case says otherwise, record both console output and `$LASTEXITCODE`.

## 8. Test cases

### A. Installation, packaging, and configuration

#### MT-CFG-001 — Clean locked installation (P0)

1. Use a machine without an existing project virtual environment.
2. Run `uv sync --locked`.
3. Run `uv run closing-signal --help`.
4. Run the automated release gate documented in the README.

Expected:

- installation resolves only the locked dependency set;
- CLI lists all ten operator commands;
- all automated gates pass without changing `uv.lock`;
- no China-market runtime dependency is installed.

Evidence: uv output, CLI output, gate output, Python/uv versions.

#### MT-CFG-002 — Valid private-beta configuration (P0)

1. Complete the common setup with valid test credentials and files.
2. Run `validate-config`.

Expected: exit code 0 and exactly one stdout JSON object,
`{"message":"configuration valid","status":"complete"}`; progress is on stderr
and no secret value is printed.

#### MT-CFG-003 — Missing and malformed configuration (P0)

Repeat `validate-config` on isolated copies with:

1. missing Alpaca/OpenFIGI/SEC/Brevo environment values;
2. an invalid sender or SEC contact address;
3. HTTP maximum delay below base delay;
4. a missing strategy, SEC rules, or subscriber file;
5. an unknown configuration key;
6. a secret inserted into TOML.

Expected: each run exits 2, names the invalid field or file, does not initialize
market data, and never echoes the secret.

#### MT-CFG-004 — Development feed boundary (P0)

1. Confirm the test profile uses the paper endpoint and IEX.
2. Capture the feed recorded by a successful ingestion run.
3. Review the release checklist for SIP-rights evidence.

Expected: IEX is recorded in development results and the candidate remains
ineligible for commercial production without written SIP rights. Configuration
alone is not treated as proof of contractual authorization.

### B. Universe and reference reconciliation

#### MT-UNI-001 — Full universe synchronization (P0)

1. Run `sync-universe` against an empty manual database.
2. Run it again for the same date.
3. Inspect summary counts and the instrument/universe tables using a read-only
   SQLite browser.

Expected:

- the first run stores a dated universe snapshot;
- the second run is idempotent and creates no duplicate instruments/members;
- only NYSE/Nasdaq common stocks, ETFs, and ADRs are accepted;
- rejected and reconciliation-warning records have explicit reasons.

#### MT-UNI-002 — Classification sample review (P0)

For every controlled reference symbol, compare the stored type/venue with:

1. the current OpenFIGI result;
2. the current Nasdaq directory entry and ETF flag;
3. the current SEC association when applicable;
4. the Alpaca asset record.

Expected: OpenFIGI supplies the type; Nasdaq and SEC reconcile it. No result is
classified from company name or ticker spelling. Source differences are warned
or quarantined instead of silently accepted.

#### MT-UNI-003 — Conflict quarantine (P0)

1. In an isolated provider fixture or offline test profile, change an ETF flag or
   venue so it conflicts with OpenFIGI/Alpaca.
2. Run universe synchronization.

Expected: the symbol is excluded, an explicit conflict is quarantined, unrelated
symbols remain accepted, and the command reports the finding.

#### MT-UNI-004 — Symbol lifecycle and punctuation (P0)

1. Inspect punctuation/share-class symbols in the stored catalog.
2. If the sample includes a renamed/delisted symbol, synchronize snapshots on
   both sides of the observed change.

Expected: symbols are preserved without six-digit assumptions; stable instrument
identity and dated symbol history are retained rather than rewriting history.

### C. Calendar and market data

#### MT-DAT-001 — Bounded historical backfill (P0)

1. On the isolated database, select a short completed date interval first.
2. Run `backfill --start YYYY-MM-DD --end YYYY-MM-DD`.
3. Rerun the identical command.
4. Inspect raw, split-adjusted, and all-adjusted rows.

Expected: all available eligible symbols are processed, retries/findings are
reported, series remain separated by adjustment key, and rerun row counts do not
grow from duplicates.

#### MT-DAT-002 — Daily synchronization and correction windows (P0)

1. Complete an initial sync through the latest finalized session.
2. Record row hashes/counts for the last five sessions and corporate actions for
   the last 63 sessions.
3. Run `sync-daily` again after the approved finalization delay.

Expected: exactly the configured session windows are requested; corrected values
replace the matching canonical keys idempotently; older immutable rows do not
change without an explicit backfill.

#### MT-DAT-003 — Session finalization and invalid dates (P0)

1. Attempt `sync-daily --session` and `screen --session` for a future or currently
   incomplete session.
2. Test the latest completed session after a weekend/holiday.
3. Test an early-close date after actual close but before/after the 150-minute delay.

Expected: incomplete/future sessions fail without final data; weekends and
holidays resolve to the prior completed session; early closes use the official
calendar close plus 150 minutes.

#### MT-DAT-004 — Bar and corporate-action reconciliation (P0)

1. Select at least ten symbol/session samples, including split/dividend events.
2. Compare stored values to freshly retrieved Alpaca responses from the recorded
   feed and adjustment mode.
3. Recalculate dollar volume using the documented definition.

Expected: timestamps/session dates, OHLCV, adjustment series, actions, and dollar
volume agree or produce a documented provider correction/finding. No raw and
adjusted value is mislabeled.

#### MT-DAT-005 — Data-quality quarantine (P0)

Using only an isolated fixture/profile, introduce duplicate, negative, invalid
OHLC, impossible volume, and missing-session records. Run `data-audit`.

Expected: suspect data is reported/quarantined with source, reason, payload hash,
first/last observation, and occurrence count; it is not silently coerced.

### D. Strategy screening and notifications

#### MT-STR-001 — Six-strategy dry run (P0)

1. Confirm all six strategies are enabled in `us-research-v1`.
2. Run `screen --session YYYY-MM-DD --dry-run` for a completed session with
   sufficient history.
3. Review each strategy's status, evaluated/skipped counts, selections, ranks,
   conditions, metrics, company labels, links, cutoff, and version.

Expected: every strategy returns complete, zero-result, partial, or failed
explicitly; output is deterministic and no email/database delivery claim is made.

#### MT-STR-002 — Deterministic rerun and reprocess (P0)

1. Save the first result/output hashes.
2. Run the same normal screen command again.
3. Run it with `--reprocess` but without notification retry authorization.

Expected: normal rerun reports existing results; reprocessing reproduces the same
selections from unchanged data and does not resend previously successful email.

#### MT-STR-003 — Point-in-time behavior (P0)

1. Screen an older completed session.
2. Inspect inputs for selected symbols and the dated universe snapshot.
3. Compare against a later session containing newer bars.

Expected: the older result uses no later bar, later universe membership, or
later cutoff information.

#### MT-NOT-001 — Controlled Brevo delivery (P0)

1. Use one controlled recipient per routing variation.
2. Run a live screen for a completed test session.
3. Inspect received plain-text and HTML alternatives and the raw message headers.

Expected: authenticated STARTTLS delivery succeeds; sender/domain are correct;
external text is escaped; links are HTTPS; no raw dataset attachment exists;
only explicitly subscribed recipients receive the category.

#### MT-NOT-002 — Delivery idempotency and recipient isolation (P0)

1. Deliver a result to two controlled recipients.
2. Rerun the same revision.
3. Cause one controlled address to fail, then run `retry-notifications`.

Expected: successful recipient/revision keys are skipped; only the failed
recipient is retried; attempts and provider responses are audited without
credentials. Do not induce failure using an address belonging to another person.

#### MT-NOT-003 — Daily send guard (P0)

Exercise the 300-attempt boundary with a local fake transport or pre-populated
isolated audit database—not by sending 300 real messages.

Expected: the next transport call is blocked, recorded as
`DailySendLimitExceeded`, and unrelated successful delivery keys remain intact.

#### MT-NOT-004 — Combined technical digest (release blocker)

1. Subscribe one controlled recipient to at least two strategies.
2. Run the completed-session live notification flow.

Target expectation: exactly one technical digest contains only that recipient's
subscribed strategy sections. Current expected status: `Expected fail` until
digest grouping is implemented.

### E. Subscriber consent and privacy

#### MT-SUB-001 — Double opt-in enforcement (P0)

Test isolated subscriber files with missing consent source, policy version,
consent timestamp, confirmation timestamp, timezone, or chronological order.

Expected: validation fails and no address is activated or contacted.

#### MT-SUB-002 — Activation, routing, and deactivation (P0)

1. Add a controlled, confirmed subscriber and synchronize state.
2. Change categories and run the relevant dry-run/live controlled flow.
3. Deactivate it with timestamp and reason, then rerun notification flows.

Expected: changes create immutable audit events; routing follows current
categories; the deactivated address receives nothing; deactivation evidence is
retained.

#### MT-SUB-003 — Private-beta unsubscribe procedure (P0)

1. Send an unsubscribe request from a controlled subscriber to the published
   monitored address.
2. Process it using the operator procedure.
3. Attempt a later notification.

Expected: deactivation and suppression are completed within one business day,
evidence is timestamped, and the later delivery is blocked.

#### MT-SUB-004 — Public one-click unsubscribe (commercial blocker)

Target expectation: one-click action deactivates the subscriber and synchronizes
Brevo suppression without another message. Current status: `Blocked` until the
hosted workflow exists.

### F. SEC monitoring

SEC tests can be expensive. First use a recent start date and an isolated,
documented issuer subset for smoke testing. The final acceptance run must use the
approved 2016 boundary and full eligible common-stock/ADR universe, respecting
SEC fair-access limits.

#### MT-SEC-001 — Candidate discovery and evidence (P0)

1. Run `sec-sync --dry-run` on the isolated smoke profile.
2. Select samples across Tier A, B, and C forms where current data permits.
3. Compare accession, CIK, issuer, symbol, form, filing/acceptance time, document
   URL, classification, confidence, evidence, and reason with SEC EDGAR.

Expected: every configured candidate is surfaced; source links resolve; unmatched
documents are `uncertain`; confidence never suppresses a candidate.

#### MT-SEC-002 — Deduplication and restart (P0)

1. Run a live controlled SEC sync and record stored accessions/delivery keys.
2. Rerun it unchanged.
3. Interrupt a later isolated run, then restart it.

Expected: no accession/result revision is delivered twice; restart continues
from persisted state; unrelated issuers continue if one issuer fails.

#### MT-SEC-003 — Classification review (P0)

Have the data reviewer examine representative registered-direct, shelf,
shelf-takedown, ATM, private-placement, convertible/warrant, employee, merger,
Regulation A, amendment, effectiveness, withdrawal, and uncertain results.

Expected: matched phrases appear in the document, labels are presented as
evidence-based classifications rather than facts beyond the filing, and ambiguous
documents remain uncertain.

#### MT-SEC-004 — Immediate versus digest routing (release blocker)

Target expectation: explicit direct-offering evidence is sent immediately; all
other/uncertain candidates appear once in the approved daily digest, with no
suppression. Current expected status: `Expected fail` until batching is implemented.

#### MT-SEC-005 — Poll schedule (release blocker)

Target expectation: every ten minutes from 06:00–22:00 ET weekdays and hourly
otherwise, including DST transitions, restart, and missed-run catch-up. Current
status: `Blocked` until production timer artifacts are implemented and deployed.

### G. Backtesting

#### MT-BT-001 — Single baseline run (P0)

1. Copy the example request and set immutable universe/data/code versions and a
   completed end date.
2. Run `backtest --request config/manual-backtest.json`.
3. Inspect manifest, trades, positions, equity curve, metrics, warnings, failures,
   and Markdown report.

Expected: the run uses SPY, $100,000, next open, 10% sizing, approved fees,
10-bps slippage, 3% risk-free rate, strategy holding period, and `fail` missing
exit policy. Artifacts reconcile internally and disclose bias/data limitations.

#### MT-BT-002 — Walk-forward isolation (P0)

1. Run the walk-forward example with immutable versions.
2. Review rolling 756/252/252/252-session folds.
3. Confirm candidate selection maximizes validation Calmar and occurs before the
   protected test segment is evaluated.

Expected: training, validation, and out-of-sample bundles are separate; every
candidate has training/validation evidence; only the selected candidate receives
an out-of-sample bundle.

#### MT-BT-003 — Cost and benchmark sensitivities (P0 before model approval)

Repeat the same immutable run with SPY/VTI and 10/25/50-bps slippage. Run a
separately labeled `mark_zero` missing-exit stress case.

Expected: only the named assumption changes; costs worsen fills in the expected
direction; reports label every sensitivity and never merge stress results with
the official baseline.

#### MT-BT-004 — Reproducibility (P0)

Run an identical request twice against unchanged database and code versions.

Expected: deterministic artifact contents/hashes except explicitly documented
run-time metadata. Any difference has an identified cause.

#### MT-BT-005 — Cancellation (P0)

Interrupt a sufficiently long isolated backtest once and wait for clean exit.

Expected: progress was visible; no complete result bundle is published; lock is
released; operation state does not claim success.

### H. Reliability, recovery, and health

#### MT-OPS-001 — Operation lock (P0)

1. Start a long mutating command on the isolated database.
2. While it runs, start another mutating command against the same database.

Expected: the second command exits 5 with `operation already running`; the first
continues without state corruption.

#### MT-OPS-002 — Provider/network failure (P0)

Temporarily block one provider in the isolated environment or use invalid test
credentials. Exercise OpenFIGI, Alpaca, SEC, and SMTP separately.

Expected: bounded retries occur; error output reveals no credentials; partial
state is not marked complete; health identifies the affected dependency; a rerun
after restoration resumes safely.

#### MT-OPS-003 — Health lifecycle (P0)

1. Run `health` on a new database.
2. Complete required operations and rerun it.
3. Separately simulate stale market data, stale/failed/running operations,
   unavailable SMTP/Alpaca, and free disk below 10 GiB.

Expected: new/degraded states return nonzero with a failed named check; a healthy
state returns 0 only when database, disk, market data, dependencies, and schedule
freshness all pass.

#### MT-OPS-004 — Process termination and restart (P0)

Terminate an isolated sync after some pages complete, verify the process is gone,
then rerun the identical command.

Expected: completed immutable pages are not unnecessarily reprocessed; operation
history exposes the interruption; the lock can be recovered only through the
documented checked procedure.

#### MT-OPS-005 — Database integrity failure (P0)

On a disposable copied database only, introduce a controlled integrity failure or
replace it with a known corrupt fixture. Run `health` and `data-audit`.

Expected: the database is never reported healthy; no notification is sent; the
operator is directed to restore rather than silently repair unknown corruption.

### I. Backup, restore, security, and retention

#### MT-BCK-001 — Consistent manual backup and restore (P0 for private beta)

1. Ensure no mutating command is running.
2. Take an encrypted consistent backup and record its hash/time.
3. Add later disposable state to the source database.
4. Restore the backup to a separate path and run integrity, health, audit, and a
   deterministic read-only backtest comparison.

Expected: the restored point matches the backup hash/state, passes integrity,
contains configuration/manifests required for reproduction, and is usable within
the four-hour RTO rehearsal target.

#### MT-BCK-002 — Automated retention and offsite restore (commercial blocker)

Target expectation: encrypted local and offsite backups retain 7 daily, 5 weekly,
and 12 monthly copies; pruning never removes required generations; quarterly
offsite restoration proves RPO 24 hours/RTO four hours. Current status: `Blocked`
until automation and the offsite destination are implemented.

#### MT-SECURITY-001 — Secret and artifact inspection (P0)

Search console logs, database text fields, reports, evidence, wheel, and source
distribution for test credential values.

Expected: no credential appears. `.env`, runtime subscriber files, local databases,
and test output are absent from distributions/source control.

#### MT-SECURITY-002 — Host permissions and encryption (P0 on target host)

1. Verify full-disk encryption, dedicated non-login or restricted service account,
   least-privilege file permissions, protected environment/secret files, UPS, and
   wired network on Ubuntu.
2. Attempt read access from an unauthorized local account.

Expected: unauthorized access to secrets, subscriber data, database, reports, and
backups is denied; the service retains only required permissions.

#### MT-SECURITY-003 — Retention review (P0 before commercial launch)

Inspect aged test records and the configured/manual pruning procedure against the
PRD: manifests seven years, delivery audit 13 months, logs 90 days, subscriber PII
active plus 30 days, consent/unsubscribe seven years, lawful suppression
indefinitely, and raw provider bodies no more than 45 days.

Expected: retained/deleted data matches policy and provider contracts; deletion
does not remove required suppression or reproducibility evidence.

### J. Performance and service-level rehearsal

#### MT-NFR-001 — Complete-universe EOD rehearsal (P0 on target host)

Run the complete finalized-session universe sync, correction, audit, screen, and
controlled digest workflow on the Ubuntu target while recording stage durations,
CPU, memory, disk, provider retries, and row counts.

Expected: the digest workflow can complete by 20:00 ET under representative load,
with no eligibility filter added to achieve the target.

#### MT-NFR-002 — SEC latency sample (P0 when routing is implemented)

For monitored direct-offering samples, measure SEC accepted timestamp to discovery
and controlled delivery across normal and degraded conditions.

Expected: at least 95% of included monthly samples complete within 20 minutes,
with exclusions documented exactly as the PRD permits.

#### MT-NFR-003 — Brevo capacity threshold (P0)

Use delivery audit metrics, not bulk test email, to model 30 days of usage.

Expected: the operator can identify when usage reaches 80% of 300 attempts on
seven of 30 days and initiate the provider upgrade procedure.

## 9. Regression suite

Run this minimum manual regression after any provider, schema, adjustment,
strategy, notification, consent, scheduler, or packaging change:

1. MT-CFG-001–004.
2. MT-UNI-001–003.
3. MT-DAT-001–004.
4. MT-STR-001–003.
5. MT-NOT-001–002.
6. MT-SUB-001–002.
7. MT-SEC-001–003.
8. MT-BT-001–002 and MT-BT-004.
9. MT-OPS-001–003.
10. MT-BCK-001 and MT-SECURITY-001.

Cases may be shortened to a bounded dataset for a pull request, but the full
universe and target-host rehearsal is required for a release candidate.

## 10. Defect severity and disposition

| Severity | Definition | Release treatment |
|---|---|---|
| 1 — Critical | Secret exposure, wrong-recipient email, corrupted canonical data, duplicate commercial alert at scale, false success, or unauthorized production feed/use | Stop testing affected path; release blocked |
| 2 — High | Missing eligible universe segment, suppressed SEC candidate, look-ahead/backtest integrity defect, failed unsubscribe, unrecoverable operation, or material data mismatch | Release blocked |
| 3 — Medium | Incorrect non-critical content, incomplete diagnostics, isolated recoverable provider case, or meaningful operator friction | Fix or obtain explicit time-bounded waiver |
| 4 — Low | Cosmetic/documentation issue without correctness, privacy, delivery, or recovery impact | May defer with owner/milestone |

An expected failure is not a pass. Record it against the known implementation
gap with an owner and target milestone.

## 11. Execution record template

Use one record per case:

```text
Test case:
Release/commit/package hash:
Environment and timezone:
Executor:
Execution start/end:
Preconditions/test data:
Commands/actions:
Expected result:
Actual result:
Exit code:
Evidence paths/hashes:
Status: Pass | Fail | Blocked | Expected fail | Not run
Defect/waiver ID:
Retest result:
Approver/date:
```

## 12. Final test report

The test lead's final report must include:

- release identifier and environment inventory;
- totals by priority and status;
- all Severity 1–4 defects and retests;
- every blocked/expected-fail case and its release impact;
- sampled data and SEC reconciliation results;
- email/consent/suppression evidence for controlled recipients;
- performance, health, backup, and restore results;
- external commercial-gate status;
- recommendation: reject, accept for development, accept for private beta, or
  accept for commercial production.
