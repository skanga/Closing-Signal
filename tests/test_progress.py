"""Human-readable progress reporting without polluting result stdout."""

import builtins
import io
from types import SimpleNamespace

from closing_signal.core import progress as progress_module
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


def test_stderr_reporter_does_not_mask_operation_when_stream_is_closed(monkeypatch) -> None:
    stream = io.StringIO()
    stream.close()
    monkeypatch.setattr(progress_module, "sys", SimpleNamespace(stderr=stream))

    StderrProgressReporter("sync-universe")(ProgressEvent("Starting"))


def test_stderr_reporter_is_silent_when_stream_is_missing(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(progress_module, "sys", SimpleNamespace(stderr=None))
    monkeypatch.setattr(
        builtins,
        "print",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    StderrProgressReporter("sync-universe")(ProgressEvent("Starting"))

    assert calls == []


def test_should_report_first_last_and_bounded_intervals() -> None:
    assert [
        completed for completed in range(1, 26) if should_report(completed, total=25, every=10)
    ] == [1, 10, 20, 25]
