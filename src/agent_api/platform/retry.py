"""Retry primitives: bounded retries with exponential backoff and jitter.

Shared technical capability (§platform): adapters use this for transient
outbound failures instead of hand-rolling their own loops.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """How to retry a transient failure."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0


def backoff_delay(policy: RetryPolicy, failed_attempt: int) -> float:
    """Delay after `failed_attempt` (1-based), exponential with full jitter."""
    capped = min(policy.base_delay * (2 ** (failed_attempt - 1)), policy.max_delay)
    return random.uniform(0, capped)


def call_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    is_transient: Callable[[Exception], bool],
) -> T:
    """Call `fn()`, retrying transient failures per the policy.

    Raises the last exception unchanged when attempts are exhausted or the
    failure is not transient. Callers translate it into their own error type.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempt += 1
            if attempt >= policy.max_attempts or not is_transient(exc):
                raise
            time.sleep(backoff_delay(policy, attempt))
