"""Platform tests (§9): retry math and behavior. Pure logic, no I/O."""

import pytest

from agent_api.platform.retry import RetryPolicy, backoff_delay, call_with_retry


def test_backoff_grows_exponentially_and_caps():
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, max_delay=3.0)
    # Jitter makes exact values random; check the caps instead.
    for _ in range(20):
        assert 0 <= backoff_delay(policy, 1) <= 1.0
        assert 0 <= backoff_delay(policy, 2) <= 2.0
        assert 0 <= backoff_delay(policy, 3) <= 3.0
        assert 0 <= backoff_delay(policy, 10) <= 3.0  # capped at max_delay


def test_retries_transient_then_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("blip")
        return "ok"

    result = call_with_retry(
        flaky,
        policy=RetryPolicy(max_attempts=3, base_delay=0),
        is_transient=lambda e: isinstance(e, ConnectionError),
    )
    assert result == "ok"
    assert len(calls) == 3


def test_gives_up_after_max_attempts():
    calls = []

    def always_fails():
        calls.append(1)
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        call_with_retry(
            always_fails,
            policy=RetryPolicy(max_attempts=3, base_delay=0),
            is_transient=lambda e: True,
        )
    assert len(calls) == 3


def test_non_transient_raises_immediately():
    calls = []

    def fails():
        calls.append(1)
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        call_with_retry(
            fails,
            policy=RetryPolicy(max_attempts=3, base_delay=0),
            is_transient=lambda e: False,
        )
    assert len(calls) == 1
