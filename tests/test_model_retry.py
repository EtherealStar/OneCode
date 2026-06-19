from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from services.errors import RetryExhaustedError
from services.model.retry import ModelRetryRunner, RetryPolicy, retry_delay_seconds
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError


def test_retry_delay_uses_exponential_backoff_and_jitter() -> None:
    policy = RetryPolicy(jitter_ratio=0.25)

    assert retry_delay_seconds(1, policy=policy, random_fraction=lambda: 0.0) == 0.5
    assert retry_delay_seconds(2, policy=policy, random_fraction=lambda: 0.0) == 1.0
    assert retry_delay_seconds(3, policy=policy, random_fraction=lambda: 1.0) == 2.5


def test_retry_delay_honors_retry_after() -> None:
    assert retry_delay_seconds(4, retry_after_seconds=7.5) == 7.5


def test_retry_runner_forwards_partial_deltas_then_retries() -> None:
    """Partial deltas from a failed attempt must reach the caller live.

    The retry runner no longer buffers attempt output. Whatever the
    provider has already streamed when it raises a retryable error is
    already visible to the caller; the retry then continues with the
    next attempt.
    """

    calls = 0
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def operation() -> AsyncIterator[ModelStreamEvent]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield ModelStreamEvent.content_delta("partial")
            raise ProviderError(
                "rate limited",
                error_type="rate_limit_error",
                status_code=429,
                retryable=True,
            )
        yield ModelStreamEvent.content_delta("final")
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": "final"},
            final_text="final",
        )

    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=2, jitter_ratio=0),
        sleep=sleep,
    )

    async def run() -> list[ModelStreamEvent]:
        return [event async for event in runner.stream(operation)]

    events = asyncio.run(run())

    assert calls == 2
    assert sleeps == [0.5]
    # The "partial" delta from the failed attempt is delivered live and
    # the successful attempt then appends "final".
    assert [event.text for event in events if event.type == "content_delta"] == [
        "partial",
        "final",
    ]
    assert events[-1].type == "message_completed"


def test_retry_runner_invokes_on_retry() -> None:
    decisions: list[tuple[str, float]] = []

    async def operation() -> AsyncIterator[ModelStreamEvent]:
        if not decisions:
            raise ProviderError(
                "network",
                error_type="network_error",
                retryable=True,
            )
        yield ModelStreamEvent.message_completed(
            assistant_message={"role": "assistant", "content": "ok"},
            final_text="ok",
        )

    async def on_retry(error: ProviderError, decision) -> None:
        decisions.append((error.error_type or "", decision.delay_seconds))

    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=1, jitter_ratio=0),
        sleep=lambda _seconds: _noop_sleep(),
    )

    async def run() -> None:
        async for _event in runner.stream(operation, on_retry=on_retry):
            pass

    asyncio.run(run())

    assert decisions == [("network_error", 0.5)]


def test_retry_runner_does_not_retry_context_limit() -> None:
    async def operation() -> AsyncIterator[ModelStreamEvent]:
        raise ProviderError(
            "too long",
            error_type="context_limit_exceeded",
            retryable=True,
        )
        yield  # pragma: no cover

    runner = ModelRetryRunner(sleep=lambda _seconds: _noop_sleep())

    async def run() -> None:
        async for _event in runner.stream(operation):
            pass

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.error_type == "context_limit_exceeded"


def test_retry_runner_raises_retry_exhausted() -> None:
    async def operation() -> AsyncIterator[ModelStreamEvent]:
        raise ProviderError("server", error_type="server_error", retryable=True)
        yield  # pragma: no cover

    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=1, jitter_ratio=0),
        sleep=lambda _seconds: _noop_sleep(),
    )

    async def run() -> None:
        async for _event in runner.stream(operation):
            pass

    with pytest.raises(RetryExhaustedError) as exc_info:
        asyncio.run(run())

    assert isinstance(exc_info.value.__cause__, ProviderError)


async def _noop_sleep() -> None:
    return None
