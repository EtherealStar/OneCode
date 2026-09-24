"""用于模型流的供应商中立重试引擎。

此运行器设计为非缓冲模式：底层供应商操作产生的事件在到达时立即转发给调用方。
之前的设计是将单次尝试的全部事件缓存在 list[ModelStreamEvent] 中并在尝试结束后重放，
这会导致 OneCode 的 UI 无法实时显示流式响应。

当前契约如下：

- stream() 立即转发从操作中收到的每个 ModelStreamEvent。
  供应商发出 content_delta 时调用方即可立即看到。
- 如果在至少产出一个事件后抛出 ProviderError，部分输出已对调用方可见；
  运行器不会假装失败的尝试从未发生。
- 运行器仍然维护重试策略（指数退避、最大重试次数、抖动、错误日志记录）。
  仍然会调用 on_retry，以便 CLI 显示可见的状态转换。
- 当可重试错误达到 max_retries 时，仍然会抛出 RetryExhaustedError。

运行器绝不会对调用方隐瞒流式文本。如果失败的尝试已经流式传输了部分内容，
该内容已到达调用方且由 UI 提交。重试语义是显式可见的，而非静默发生。
"""

from __future__ import annotations

import asyncio
import inspect
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

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
    policy: RetryPolicy | None = None,
    random_fraction: Callable[[], float] | None = None,
) -> float:
    if policy is None:
        policy = RetryPolicy()
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
            # 不再在缓冲区中累积事件，事件在供应商产出后立即转发给调用方。
            # 这是 CLI 显示实时流式文本的唯一方式。
            forwarded_any = False
            try:
                async for event in operation():
                    forwarded_any = True
                    yield event
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
                                "partial_output_visible": forwarded_any,
                            },
                        )
                        exhausted.__cause__ = exc
                        self.error_log_recorder.record_error(
                            exhausted,
                            source="model_retry",
                            attributes={
                                "attempt": attempt,
                                "partial_output_visible": forwarded_any,
                            },
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
                        "partial_output_visible": forwarded_any,
                    },
                )
                self.error_log_recorder.record_error(
                    exc,
                    source="model_retry",
                    attributes={
                        "attempt": decision.attempt,
                        "max_retries": decision.max_retries,
                        "delay_seconds": decision.delay_seconds,
                        "partial_output_visible": forwarded_any,
                    },
                )
                if on_retry is not None:
                    maybe_awaitable = on_retry(exc, decision)
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                await self._sleep(decision.delay_seconds)
                attempt += 1
                continue

            # 操作完成且未抛出异常，结束执行。
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
