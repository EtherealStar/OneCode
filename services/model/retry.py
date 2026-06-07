"""Provider-neutral retry engine for model streams."""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from services.errors import RetryExhaustedError
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.observability import ErrorLogRecorder, TraceRecorder

RATE_LIMIT_RETRY_TRANSITION = "rate_limit_retry"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 10
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 32.0
    jitter_ratio: float = 0.25


@dataclass(frozen=True)
class RetryDecision:
    attempt: int
    max_retries: int
    delay_seconds: float
    transition: str
    reason: str


def retry_delay_seconds(
    attempt: int,
    *,
    retry_after_seconds: float | None = None,
    policy: RetryPolicy = RetryPolicy(),
    random_fraction: Callable[[], float] | None = None,
) -> float:
    if retry_after_seconds is not None:
        return max(0.0, retry_after_seconds)
    base = min(
        policy.base_delay_seconds * (2 ** max(0, attempt - 1)),
        policy.max_delay_seconds,
    )
    if policy.jitter_ratio <= 0:
        return base
    fraction = random_fraction() if random_fraction is not None else random.random()
    return base + (max(0.0, min(1.0, fraction)) * policy.jitter_ratio * base)


class ModelRetryRunner:
    def __init__(
        self,
        *,
        policy: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        trace_recorder: TraceRecorder | None = None,
        error_log_recorder: ErrorLogRecorder | None = None,
        random_fraction: Callable[[], float] | None = None,
    ) -> None:
        self.policy = policy or RetryPolicy()
        self._sleep = sleep or asyncio.sleep
        self.trace_recorder = trace_recorder or TraceRecorder.noop()
        self.error_log_recorder = error_log_recorder or ErrorLogRecorder.noop()
        self._random_fraction = random_fraction

    async def stream(
        self,
        operation: Callable[[], AsyncIterator[ModelStreamEvent]],
        *,
        on_retry: Callable[
            [ProviderError, RetryDecision],
            Awaitable[None] | None,
        ]
        | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        attempt = 1
        while True:
            buffer: list[ModelStreamEvent] = []
            try:
                async for event in operation():
                    buffer.append(event)
            except ProviderError as exc:
                if not self._should_retry(exc) or attempt > self.policy.max_retries:
                    if attempt > self.policy.max_retries and self._should_retry(exc):
                        exhausted = RetryExhaustedError(
                            "Provider retry attempts exhausted.",
                            metadata={
                                "attempt": attempt,
                                "max_retries": self.policy.max_retries,
                                "error_type": exc.error_type,
                                "provider_id": exc.provider_id,
                            },
                        )
                        exhausted.__cause__ = exc
                        self.error_log_recorder.record_error(
                            exhausted,
                            source="model_retry",
                            attributes={"attempt": attempt},
                        )
                        raise exhausted from exc
                    raise

                decision = self._decision(attempt, exc)
                self.trace_recorder.event(
                    "model_retry",
                    {
                        "attempt": decision.attempt,
                        "max_retries": decision.max_retries,
                        "delay_seconds": decision.delay_seconds,
                        "error_type": exc.error_type,
                        "provider_id": exc.provider_id,
                        "status_code": exc.status_code,
                    },
                )
                self.error_log_recorder.record_error(
                    exc,
                    source="model_retry",
                    attributes={
                        "attempt": decision.attempt,
                        "max_retries": decision.max_retries,
                        "delay_seconds": decision.delay_seconds,
                    },
                )
                if on_retry is not None:
                    maybe_awaitable = on_retry(exc, decision)
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                await self._sleep(decision.delay_seconds)
                attempt += 1
                continue

            for event in buffer:
                yield event
            return

    def _should_retry(self, error: ProviderError) -> bool:
        return error.retryable is True and error.error_type != "context_limit_exceeded"

    def _decision(self, attempt: int, error: ProviderError) -> RetryDecision:
        delay = retry_delay_seconds(
            attempt,
            retry_after_seconds=error.retry_after_seconds,
            policy=self.policy,
            random_fraction=self._random_fraction,
        )
        return RetryDecision(
            attempt=attempt,
            max_retries=self.policy.max_retries,
            delay_seconds=delay,
            transition=RATE_LIMIT_RETRY_TRANSITION,
            reason=error.error_type or "provider_error",
        )
