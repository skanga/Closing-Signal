"""Retry-policy validation and transient/non-transient behavior."""

import pytest
import requests

from closing_signal.core.http import RetryPolicy, call_with_retry


@pytest.mark.parametrize(
    "values",
    [
        (0, 0.0, 0.0, 0.0),
        (1, -1.0, 1.0, 0.0),
        (1, 2.0, 1.0, 0.0),
    ],
)
def test_invalid_retry_policies_are_rejected(values) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(*values)


def test_non_transient_client_error_is_not_retried() -> None:
    response = requests.Response()
    response.status_code = 400
    error = requests.HTTPError(response=response)
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        raise error

    with pytest.raises(requests.HTTPError):
        call_with_retry(
            fail,
            RetryPolicy(3, 0, 0, 0),
            sleep=lambda delay: None,
        )

    assert calls == 1


def test_transient_error_retries_then_returns() -> None:
    calls = 0
    delays: list[float] = []

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("temporary")
        return "ok"

    result = call_with_retry(
        operation,
        RetryPolicy(3, 1, 4, 0),
        sleep=delays.append,
    )

    assert result == "ok"
    assert delays == [1, 2]
