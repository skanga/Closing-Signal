# CLI Progress and Universe Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make long-running commands visibly active, repair OpenFIGI universe mapping, and return actionable bounded diagnostics when universe synchronization fails.

**Architecture:** Introduce a dependency-free progress callback boundary in `closing_signal.core.progress`. The CLI binds that callback to a flushed stderr reporter, while providers, ingestion services, and operations emit structured milestones without printing directly. OpenFIGI uses the supported U.S. ticker mapping contract, and `sync-universe` aggregates current-run rejection evidence into its final stdout JSON.

**Tech Stack:** Python 3.12+, argparse, dataclasses, typing protocols, pytest, Ruff, Black, mypy

---

## File map

- Create `closing_signal/core/progress.py`: immutable progress events, reporter protocol, no-op reporter, stderr formatter, bounded-interval predicate.
- Create `tests/test_progress.py`: progress formatting, flushing, failure isolation, and interval tests.
- Modify `closing_signal/cli.py`: create the command-bound reporter, emit the immediate start event, and pass reporters to operation handlers.
- Modify `closing_signal/providers/reference.py`: correct OpenFIGI mapping jobs, retain provider errors, and report batch/reconciliation progress.
- Modify `closing_signal/operations.py`: thread progress through workflows, add universe rejection diagnostics, and emit operation milestones.
- Modify `closing_signal/data/ingestion.py`: report market-data chunk progress.
- Modify `closing_signal/data/repository.py`: report audit stages around expensive queries and quarantine persistence.
- Modify `tests/test_cli.py`, `tests/test_reference_provider.py`, `tests/test_ingestion.py`, `tests/test_operations.py`, and `tests/test_repository.py`: regression and progress coverage.
- Modify `README.md` and `docs/OPERATIONS.md`: document stderr progress, stdout summaries, redirection, and universe diagnostics.

### Task 1: Core progress contract and CLI stream separation

**Files:**
- Create: `closing_signal/core/progress.py`
- Create: `tests/test_progress.py`
- Modify: `closing_signal/cli.py:1-153`
- Modify: `closing_signal/operations.py:95-655`
- Modify: `tests/test_cli.py:1-101`

- [ ] **Step 1: Write failing tests for the progress primitives**

Create `tests/test_progress.py`:

```python
"""Human-readable progress reporting without polluting result stdout."""

import builtins

from closing_signal.core.progress import (
    ProgressEvent,
    StderrProgressReporter,
    should_report,
)


def test_stderr_reporter_formats_counts_and_flushes(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(
        builtins,
        "print",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    StderrProgressReporter("sync-universe")(
        ProgressEvent("Classifying assets", completed=10, total=106, unit="batches")
    )

    assert calls[0][0] == ("[sync-universe] Classifying assets: 10/106 batches",)
    assert calls[0][1]["flush"] is True
    assert calls[0][1]["file"] is not None


def test_stderr_reporter_does_not_mask_operation_when_stderr_fails(monkeypatch) -> None:
    def fail(*args, **kwargs) -> None:
        del args, kwargs
        raise OSError("closed stream")

    monkeypatch.setattr(builtins, "print", fail)

    StderrProgressReporter("sync-universe")(ProgressEvent("Starting"))


def test_should_report_first_last_and_bounded_intervals() -> None:
    assert [
        completed
        for completed in range(1, 26)
        if should_report(completed, total=25, every=10)
    ] == [1, 10, 20, 25]
```

- [ ] **Step 2: Run the primitive tests and verify they fail**

Run: `uv run pytest tests/test_progress.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'closing_signal.core.progress'`.

- [ ] **Step 3: Implement the progress primitives**

Create `closing_signal/core/progress.py`:

```python
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
        try:
            print(f"[{self.operation}] {event.render()}", file=sys.stderr, flush=True)
        except OSError:
            return


def should_report(completed: int, *, total: int, every: int) -> bool:
    """Bound logs while always reporting the first and final units."""
    if completed < 1 or total < 1 or every < 1:
        raise ValueError("progress counts and interval must be positive")
    return completed == 1 or completed == total or completed % every == 0
```

- [ ] **Step 4: Run the primitive tests and verify they pass**

Run: `uv run pytest tests/test_progress.py -v`

Expected: `3 passed`.

- [ ] **Step 5: Write a failing CLI stream-separation test and update handler test doubles**

In `tests/test_cli.py`, add `import json`, import `ProgressEvent`, update both
monkeypatched operation lambdas to accept `progress`, and add:

```python
def test_run_writes_progress_to_stderr_and_result_to_stdout(
    tmp_path, monkeypatch, capsys
) -> None:
    database = tmp_path / "market.db"
    monkeypatch.setattr(cli, "load_settings", lambda path: SimpleNamespace(database_path=database))

    def handler(args, settings, repository, progress) -> int:
        del args, settings, repository
        progress(ProgressEvent("Working", completed=1, total=2, unit="steps"))
        print(json.dumps({"status": "complete"}))
        return 0

    monkeypatch.setitem(cli._OPERATION_HANDLERS, "sync-universe", handler)

    assert run(["--config", "settings.json", "sync-universe"]) == 0
    captured = capsys.readouterr()

    assert captured.out == '{"status": "complete"}\n'
    assert captured.err.splitlines() == [
        "[sync-universe] Starting",
        "[sync-universe] Working: 1/2 steps",
    ]
```

Change existing lambdas from `lambda args, settings, repository: 0` to
`lambda args, settings, repository, progress: 0`, including the
`selected_repository` variant.

- [ ] **Step 6: Run the CLI test and verify it fails**

Run: `uv run pytest tests/test_cli.py::test_run_writes_progress_to_stderr_and_result_to_stdout -v`

Expected: failure because `run()` neither emits `Starting` nor passes a reporter.

- [ ] **Step 7: Wire the progress reporter through CLI dispatch**

In `closing_signal/cli.py`, import the progress types, construct the reporter
immediately after parsing, and pass it as the fourth handler argument:

```python
from closing_signal.core.progress import ProgressEvent, ProgressReporter, StderrProgressReporter

# In run(), immediately after parse_args:
progress = StderrProgressReporter(args.command)
progress(ProgressEvent("Starting"))

# At mutating handler dispatch:
status = handler(args, settings, repository, progress)

_OPERATION_HANDLERS: dict[
    str,
    Callable[[argparse.Namespace, AppSettings, SQLiteRepository, ProgressReporter], int],
] = {
    # existing command mapping remains unchanged
}
```

Keep `health` on its existing three-argument call: it receives the immediate
CLI start event but has no long-running inner loop requiring a callback.

In `closing_signal/operations.py`, import `ProgressReporter` and `no_progress`.
Add `progress: ProgressReporter = no_progress` as the fourth positional
parameter of every function stored in `_OPERATION_HANDLERS`: `sync_universe`,
`sync_daily`, `backfill`, `screen`, `sec_sync`, `retry_notifications`,
`run_backtest`, and `data_audit`. Do not emit from these functions yet; this
step makes CLI dispatch type-safe while retaining three-argument compatibility
for direct library callers.

- [ ] **Step 8: Run focused tests and commit**

Run: `uv run pytest tests/test_progress.py tests/test_cli.py -v`

Expected: all progress and CLI tests pass.

```bash
git add closing_signal/core/progress.py closing_signal/cli.py closing_signal/operations.py tests/test_progress.py tests/test_cli.py
git commit -m "Add CLI progress reporting contract"
```

### Task 2: Repair and instrument OpenFIGI classification

**Files:**
- Modify: `closing_signal/providers/reference.py:1-156,220-285,344-356`
- Modify: `closing_signal/operations.py:63-92`
- Modify: `tests/test_reference_provider.py:1-132`

- [ ] **Step 1: Change the OpenFIGI contract assertion and add provider-error tests**

In `test_openfigi_maps_explicit_types_and_refuses_ambiguous_results`, replace
the expected first job with:

```python
assert jobs[0] == {
    "idType": "TICKER",
    "idValue": "AAPL",
    "exchCode": "US",
    "marketSecDes": "Equity",
}
```

Add:

```python
def test_openfigi_preserves_actionable_provider_errors_and_warnings() -> None:
    session = StubSession(
        [[{"error": "securityType2 required"}, {"warning": "No identifier found."}]]
    )
    client = OpenFigiClient(api_key="openfigi-key", session=session, request_interval=0)

    result = client.fetch_classifications([_asset("AAPL"), _asset("MISSING")])

    assert result.issues == {
        "AAPL": "OpenFIGI API error: securityType2 required",
        "MISSING": "OpenFIGI API warning: No identifier found.",
    }


def test_openfigi_reports_bounded_batch_progress() -> None:
    assets = [_asset(f"SYM{index:03}") for index in range(101)]
    mapped = {"data": [{"securityType": "Common Stock"}]}
    session = StubSession([[mapped] * 100, [mapped]])
    events = []
    client = OpenFigiClient(
        api_key="openfigi-key",
        session=session,
        request_interval=0,
        progress=events.append,
    )

    client.fetch_classifications(assets)

    assert [(event.completed, event.total, event.unit) for event in events] == [
        (1, 2, "batches"),
        (2, 2, "batches"),
    ]
```

- [ ] **Step 2: Run the OpenFIGI tests and verify they fail**

Run: `uv run pytest tests/test_reference_provider.py -v`

Expected: the request-shape assertion fails, provider errors remain generic, and
`OpenFigiClient` rejects the new `progress` argument.

- [ ] **Step 3: Correct request construction and preserve sanitized API issues**

In `closing_signal/providers/reference.py`, import the progress types, remove the
unused `_MIC` mapping and `ClassVar` import, accept
`progress: ProgressReporter = no_progress`, and store it on the client. Build
jobs as:

```python
jobs = [
    {
        "idType": "TICKER",
        "idValue": symbol,
        "exchCode": "US",
        "marketSecDes": "Equity",
    }
    for symbol, _exchange in batch
]
```

Before each reported batch, use:

```python
total_batches = (len(valid_assets) + 99) // 100
batch_number = batch_index + 1
if should_report(batch_number, total=total_batches, every=10):
    self.progress(
        ProgressEvent(
            "Classifying assets with OpenFIGI",
            completed=batch_number,
            total=total_batches,
            unit="batches",
        )
    )
```

At the start of `_map_openfigi_result`, preserve bounded provider messages:

```python
if isinstance(raw_result, dict):
    for key in ("error", "warning"):
        value = raw_result.get(key)
        if isinstance(value, str) and value.strip():
            message = " ".join(value.split())[:500]
            return f"OpenFIGI API {key}: {message}"
```

Retain the existing structural and type-conflict checks after this block.

- [ ] **Step 4: Report reconciliation stages and inject the callback from the builder**

Add `progress: ProgressReporter = no_progress` to
`ReconciledAssetClassifier.__init__`, save it, and emit these events immediately
before the corresponding fetches in `prepare()`:

```python
self.progress(ProgressEvent("Classifying the Alpaca catalog with OpenFIGI"))
primary = self.primary.fetch_classifications(assets)
self.progress(ProgressEvent("Reconciling the Nasdaq listing directories"))
nasdaq = self.nasdaq.fetch_references()
self.progress(ProgressEvent("Reconciling SEC company ticker associations"))
sec = self.sec.fetch_references()
```

In `closing_signal/operations.py`, change `_asset_classifier` and `build_alpaca`
to accept `progress: ProgressReporter = no_progress`, then pass it to both
`OpenFigiClient(progress=progress)` and
`ReconciledAssetClassifier(..., progress=progress)`.

- [ ] **Step 5: Run provider tests and commit**

Run: `uv run pytest tests/test_reference_provider.py tests/test_alpaca_provider.py -v`

Expected: all reference and Alpaca provider tests pass.

```bash
git add closing_signal/providers/reference.py closing_signal/operations.py tests/test_reference_provider.py
git commit -m "Fix OpenFIGI universe classification"
```

### Task 3: Actionable universe-sync progress and failure diagnostics

**Files:**
- Modify: `closing_signal/operations.py:1-129`
- Modify: `tests/test_operations.py:97-112`

- [ ] **Step 1: Add a failing zero-accepted diagnostic test**

Add to `tests/test_operations.py`:

```python
def test_sync_universe_failure_reports_bounded_actionable_reasons(
    tmp_path, monkeypatch, capsys
) -> None:
    repository = SQLiteRepository(tmp_path / "market.db")
    rejected = tuple(
        [RejectedInstrument(f"OTC{index}", "venue is not NYSE or Nasdaq") for index in range(5)]
        + [RejectedInstrument("AAPL", "OpenFIGI API error: invalid request")]
    )
    client = SimpleNamespace(
        fetch_instruments=lambda observed_on: InstrumentFetchResult((), rejected)
    )
    monkeypatch.setattr(
        "closing_signal.operations.build_alpaca",
        lambda settings, progress: client,
    )
    events = []

    status = sync_universe(
        argparse.Namespace(as_of=date(2026, 8, 19)),
        SimpleNamespace(),
        repository,
        events.append,
    )
    summary = json.loads(capsys.readouterr().out)

    assert status == 4
    assert summary["rejection_reasons"] == [
        {
            "reason": "venue is not NYSE or Nasdaq",
            "count": 5,
            "examples": ["OTC0", "OTC1", "OTC2"],
        },
        {
            "reason": "OpenFIGI API error: invalid request",
            "count": 1,
            "examples": ["AAPL"],
        },
    ]
    assert "rerun sync-universe" in summary["next_step"]
    assert [event.message for event in events] == [
        "Fetching the Alpaca asset catalog",
        "Persisting instruments and quarantine findings",
    ]
```

Update the existing successful sync test's monkeypatch to
`lambda settings, progress: client`.

- [ ] **Step 2: Run the diagnostic test and verify it fails**

Run: `uv run pytest tests/test_operations.py::test_sync_universe_failure_reports_bounded_actionable_reasons -v`

Expected: failure because `sync_universe` has no progress argument or diagnostic fields.

- [ ] **Step 3: Implement stable bounded rejection aggregation**

Import `Counter`, `defaultdict`, `Sequence`, `RejectedInstrument`, and the
progress types in `operations.py`, then add:

```python
def _rejection_reasons(
    rejected: Sequence[RejectedInstrument],
) -> list[dict[str, object]]:
    counts = Counter(item.reason for item in rejected)
    examples: defaultdict[str, list[str]] = defaultdict(list)
    for item in rejected:
        selected = examples[item.reason]
        if item.provider_symbol not in selected and len(selected) < 3:
            selected.append(item.provider_symbol)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return [
        {"reason": reason, "count": count, "examples": examples[reason]}
        for reason, count in ordered
    ]
```

Use the existing `sync_universe` progress parameter from Task 1, pass it into
`build_alpaca`, and emit:

```python
progress(ProgressEvent("Fetching the Alpaca asset catalog"))
result = client.fetch_instruments(observed_on=observed_on)
progress(ProgressEvent("Persisting instruments and quarantine findings"))
```

When `result.accepted` is empty, add:

```python
summary["rejection_reasons"] = _rejection_reasons(result.rejected)
summary["next_step"] = (
    "Review rejection_reasons and provider credentials, then rerun sync-universe."
)
```

- [ ] **Step 4: Run universe operation tests and commit**

Run: `uv run pytest tests/test_operations.py -k sync_universe -v`

Expected: successful and failed universe tests pass.

```bash
git add closing_signal/operations.py tests/test_operations.py
git commit -m "Add actionable universe sync diagnostics"
```

### Task 4: Market-data ingestion progress

**Files:**
- Modify: `closing_signal/data/ingestion.py:1-210`
- Modify: `closing_signal/operations.py:132-161,658-722`
- Modify: `tests/test_ingestion.py`
- Modify: `tests/test_operations.py:115-158`

- [ ] **Step 1: Add a failing ingestion chunk-progress test**

In the existing successful ingestion test, pass `progress=events.append` to the
service, use three symbols with `chunk_size=2`, and assert:

```python
assert [
    (event.message, event.completed, event.total, event.unit)
    for event in events
] == [
    ("Fetching raw daily bars", 1, 2, "chunks"),
    ("Fetching raw daily bars", 2, 2, "chunks"),
]
```

- [ ] **Step 2: Run the ingestion test and verify it fails**

Run: `uv run pytest tests/test_ingestion.py -v`

Expected: `MarketDataIngestionService` rejects the `progress` argument.

- [ ] **Step 3: Emit one event per existing provider chunk**

Add `progress: ProgressReporter = no_progress` to the ingestion service
constructor and store it. In `sync()`, compute and report chunks:

```python
total_chunks = (len(symbols) + self.chunk_size - 1) // self.chunk_size
for chunk_number, offset in enumerate(
    range(0, len(symbols), self.chunk_size), start=1
):
    chunk = symbols[offset : offset + self.chunk_size]
    self.progress(
        ProgressEvent(
            f"Fetching {self.adjustment} daily bars",
            completed=chunk_number,
            total=total_chunks,
            unit="chunks",
        )
    )
    # existing page-key, resume, fetch, validation, and persistence logic follows
```

An already completed chunk still emits its milestone before being counted as
skipped, so a resumed run remains visibly active.

- [ ] **Step 4: Thread progress through daily sync and backfill**

Use the existing `sync_daily` and `backfill` progress parameters from Task 1,
then add a required `progress: ProgressReporter` keyword parameter to
`_sync_range`. Pass it from both operations and into every
`MarketDataIngestionService(progress=progress)` instance. Emit these outer
milestones:

```python
progress(ProgressEvent("Resolving completed exchange sessions"))
progress(ProgressEvent(f"Synchronizing {adjustment} daily-bar series"))
progress(ProgressEvent("Refreshing corporate actions"))
```

During corporate-action chunks, emit `ProgressEvent("Fetching corporate actions",
completed=chunk_number, total=total_chunks, unit="chunks")`.

- [ ] **Step 5: Extend the daily-sync operation test**

Pass `events.append` to `sync_daily`; its existing one-argument `build_alpaca`
test double remains valid because only universe sync injects provider-level
progress. Assert the messages include all of:

```python
assert {
    "Resolving completed exchange sessions",
    "Synchronizing raw daily-bar series",
    "Synchronizing split daily-bar series",
    "Synchronizing all daily-bar series",
    "Refreshing corporate actions",
}.issubset({event.message for event in events})
```

- [ ] **Step 6: Run ingestion and operation tests and commit**

Run: `uv run pytest tests/test_ingestion.py tests/test_operations.py -k 'ingestion or sync_daily' -v`

Expected: all selected tests pass.

```bash
git add closing_signal/data/ingestion.py closing_signal/operations.py tests/test_ingestion.py tests/test_operations.py
git commit -m "Report market data ingestion progress"
```

### Task 5: Remaining long-running operation milestones

**Files:**
- Modify: `closing_signal/operations.py:164-655`
- Modify: `closing_signal/data/repository.py:1040-1095`
- Modify: `tests/test_operations.py`
- Modify: `tests/test_repository.py:158-174`

- [ ] **Step 1: Add failing progress assertions to existing operation tests**

For the existing screen, SEC sync, and single-backtest operation tests, create
an `events` list, pass `events.append` as the fourth operation argument, and add:

```python
assert "Preparing point-in-time screening data" in {event.message for event in screen_events}
assert any(event.message.startswith("Evaluating strategy ") for event in screen_events)

assert "Loading SEC issuer references" in {event.message for event in sec_events}
assert any(event.message == "Checking eligible SEC issuers" for event in sec_events)

assert "Loading the backtest request and stored inputs" in {
    event.message for event in backtest_events
}
assert any(event.message == "Evaluating backtest sessions" for event in backtest_events)
```

In `test_data_audit_quarantines_missing_series_and_inconsistent_factors`, define
`audit_events = []`, pass `audit_events.append` to
`repository.run_data_audit`, and add:

```python
assert [event.message for event in audit_events] == [
    "Scanning for incomplete adjustment series",
    "Checking split-factor consistency",
    "Persisting data-quality findings",
]
```

Add this retry-notification unit test:

```python
def test_retry_notifications_forwards_progress(monkeypatch) -> None:
    selected: dict[str, object] = {}

    def fake_screen(args, settings, repository, progress) -> int:
        del args, settings, repository
        selected["progress"] = progress
        return 0

    monkeypatch.setattr("closing_signal.operations.screen", fake_screen)
    reporter = lambda event: None

    status = retry_notifications(
        argparse.Namespace(session=date(2026, 1, 3), dry_run=True),
        SimpleNamespace(),
        object(),
        reporter,
    )

    assert status == 0
    assert selected["progress"] is reporter
```

- [ ] **Step 2: Run the selected tests and verify they fail**

Run: `uv run pytest tests/test_operations.py tests/test_repository.py -k 'screen or sec_sync or backtest or data_audit or retry_notifications' -v`

Expected: failures because these functions do not yet emit progress or forward
the callback.

- [ ] **Step 3: Instrument screen and notification retry**

Use the existing `screen` and `retry_notifications` progress parameters from
Task 1. Before building the data view, emit:

```python
progress(ProgressEvent("Preparing point-in-time screening data"))
```

Change the strategy loop to `enumerate(strategies, start=1)` and emit:

```python
progress(
    ProgressEvent(
        f"Evaluating strategy {strategy.strategy_id}",
        completed=strategy_number,
        total=len(strategies),
        unit="strategies",
    )
)
```

Pass `progress` unchanged from `retry_notifications` into `screen`.

- [ ] **Step 4: Instrument bounded SEC issuer progress**

Use the existing `sec_sync` progress parameter from Task 1. Emit `Loading SEC
issuer references` before `fetch_company_tickers()`. Build a tuple
of eligible common stocks and ADRs, enumerate it, and report first, every 100th,
and last issuer:

```python
eligible = tuple(
    instrument
    for instrument in repository.list_instruments()
    if instrument.instrument_type.value in eligible_types
)
for issuer_number, instrument in enumerate(eligible, start=1):
    if should_report(issuer_number, total=len(eligible), every=100):
        progress(
            ProgressEvent(
                "Checking eligible SEC issuers",
                completed=issuer_number,
                total=len(eligible),
                unit="issuers",
            )
        )
    # existing CIK, discovery, classification, and delivery logic follows
```

If `eligible` is empty, emit `ProgressEvent("No eligible SEC issuers found")`
and retain the existing final summary behavior.

- [ ] **Step 5: Route bounded backtest progress to the callback**

Use the existing `run_backtest` progress parameter from Task 1, emit `Loading
the backtest request and stored inputs`, and replace the hard-coded printer with
a callback adapter:

```python
def _backtest_progress(reporter: ProgressReporter):
    def report(event: BacktestProgress) -> None:
        if event.completed_sessions % 25 and event.completed_sessions != event.total_sessions:
            return
        reporter(
            ProgressEvent(
                "Evaluating backtest sessions",
                completed=event.completed_sessions,
                total=event.total_sessions,
                unit="sessions",
            )
        )

    return report
```

Pass `_backtest_progress(progress)` to both `BacktestEngine.run` and
`WalkForwardExperiment`. Remove the former stdout JSON progress printer so
stdout ends with only the final artifact summary.

- [ ] **Step 6: Instrument repository audit stages**

Add `progress: ProgressReporter = no_progress` to
`SQLiteRepository.run_data_audit`. Emit the three tested events immediately
before the incomplete-series query, the factor-comparison query, and the two
quarantine loops respectively. Use the existing `data_audit` operation progress
parameter from Task 1 and call `repository.run_data_audit(progress)`.

- [ ] **Step 7: Run focused tests and commit**

Run: `uv run pytest tests/test_operations.py tests/test_repository.py tests/test_backtest.py -v`

Expected: all selected tests pass and backtest engine cancellation coverage is unchanged.

```bash
git add closing_signal/operations.py closing_signal/data/repository.py tests/test_operations.py tests/test_repository.py
git commit -m "Report long-running operation milestones"
```

### Task 6: Document the operator-facing stream contract

**Files:**
- Modify: `README.md:83-194`
- Modify: `docs/OPERATIONS.md:20-70`

- [ ] **Step 1: Update README command guidance**

Add this paragraph below `## Operator workflow`:

````markdown
Long-running commands print flushed, human-readable progress to stderr and one
final machine-readable summary to stdout. Redirect the streams independently
when running under a scheduler:

```powershell
uv run closing-signal --config config/settings.toml sync-universe `
  1>sync-universe-result.json 2>sync-universe-progress.log
```

If `sync-universe` accepts no instruments, its final JSON includes the five most
common rejection reasons, up to three sample symbols per reason, and a suggested
next step. Provider credentials and raw responses are never printed.
````

- [ ] **Step 2: Update the operations guide**

Add this paragraph after the completed-session command sequence:

```markdown
Progress lines are written to stderr and flushed as work advances; final command
summaries remain on stdout. Capture both streams in scheduled jobs. A lack of
new progress beyond the normal provider timeout/retry window is actionable, as
is any nonzero final exit status. Universe-sync failures include bounded current-
run reason counts and sample symbols; investigate those fields before rerunning.
```

- [ ] **Step 3: Check documentation formatting and commit**

Run: `git diff --check -- README.md docs/OPERATIONS.md`

Expected: exit status 0 and no whitespace errors.

```bash
git add README.md docs/OPERATIONS.md
git commit -m "Document CLI progress output"
```

### Task 7: Full verification and live contract check

**Files:**
- Verify all modified files

- [ ] **Step 1: Run formatting and static analysis**

Run:

```bash
uv run black --check .
uv run ruff check .
uv run mypy
```

Expected: all three commands exit 0 with no formatting, lint, or type errors.

- [ ] **Step 2: Run the complete test suite with the repository coverage gate**

Run: `uv run pytest --cov=closing_signal --cov-report=term-missing --cov-fail-under=80`

Expected: all tests pass and total coverage is at least 80%.

- [ ] **Step 3: Verify the CLI stream boundary without external credentials**

Run:

```bash
uv run closing-signal --config config/settings.example.toml validate-config \
  1>/tmp/closing-signal-stdout.txt 2>/tmp/closing-signal-stderr.txt || true
sed -n '1,10p' /tmp/closing-signal-stderr.txt
sed -n '1,10p' /tmp/closing-signal-stdout.txt
```

Expected: stderr begins with `[validate-config] Starting`; stdout contains only
the existing validation result. No credential value appears in either stream.

- [ ] **Step 4: Verify the repaired OpenFIGI contract with one configured symbol**

Run a one-symbol provider probe from the worktree while explicitly loading the
primary checkout's `.env`; print only the classification and issue mapping,
never the key. Expected: AAPL maps to `common_stock` and has no issue.

Then, from `/home/skanga/Closing-Signal`, run the worktree implementation against
the operator-selected runtime config:

```bash
uv run --project .worktrees/cli-progress-universe-sync --env-file .env \
  closing-signal --config config/settings.toml sync-universe
```

Expected: progress arrives on stderr before the final stdout JSON, `accepted` is
greater than zero, and failures—if any—contain bounded actionable reasons.

- [ ] **Step 5: Inspect the final branch and commit any verification-only fixes**

Run:

```bash
git status --short
git log --oneline --decorate -8
git diff main...HEAD --check
```

Expected: only intentional feature commits are present, the worktree is clean,
and the branch diff has no whitespace errors. If verification required a code
change, repeat its focused red/green test and commit only that fix before this
inspection.
