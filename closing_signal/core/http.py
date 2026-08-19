"""Bounded exponential retry policy for transient external HTTP failures."""

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Explicit retry count, exponential cap, and additive jitter."""

    max_attempts: int
    base_delay_seconds: float
    max_delay_seconds: float
    jitter_seconds: float

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if min(self.base_delay_seconds, self.max_delay_seconds, self.jitter_seconds) < 0:
            raise ValueError("retry delays cannot be negative")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds cannot be less than base_delay_seconds")


def call_with_retry[ResultT](
    operation: Callable[[], ResultT],
    policy: RetryPolicy | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
) -> ResultT:
    """Retry transient connection and HTTP failures, then re-raise the last one."""
    attempts = policy.max_attempts if policy else 1
    for attempt in range(attempts):
        try:
            return operation()
        except (ConnectionError, TimeoutError, requests.RequestException) as exc:
            if not _transient(exc) or attempt + 1 >= attempts:
                raise
            assert policy is not None
            exponential = policy.base_delay_seconds * (2**attempt)
            delay = min(exponential, policy.max_delay_seconds)
            delay += random_value() * policy.jitter_seconds
            sleep(delay)
    raise AssertionError("retry loop exhausted without returning or raising")


def _transient(exc: BaseException) -> bool:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return True
