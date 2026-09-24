"""SDK 模型客户端的错误、重试与跨轮协议测试。

每个 SDK 连接、超时、状态码与解码失败都必须以供应商中立的
`ProviderError` 到达调用方；`CancelledError` 原样传播；
适配器自身不进行重试。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sdk_test_support import (
    Recorder,
    async_sdk,
    error_response,
    resolved_config,
    sse_response,
    text_chunk,
)

from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
)
from services.context.snapshot import ContextSnapshot
from services.model.retry import ModelRetryRunner, RetryPolicy
from services.model.types import ProviderError


def run_stream(
    handler: Any,
    *,
    snapshot: ContextSnapshot | None = None,
    config: Any | None = None,
) -> tuple[list[Any], BaseException | None]:
    config = config or resolved_config()
    sdk = async_sdk(config, handler)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)
    events: list[Any] = []
    error: BaseException | None = None

    async def run() -> None:
        nonlocal error
        try:
            async for event in client.stream(snapshot or ContextSnapshot("", ())):
                events.append(event)
        except BaseException as exc:  # noqa: BLE001 - 测试需要检查边界异常类型
            error = exc
        finally:
            await sdk.close()

    asyncio.run(run())
    return events, error


@pytest.mark.parametrize(
    ("status_code", "expected_type", "retryable"),
    [
        (401, "authentication_error", False),
        (403, "authentication_error", False),
        (429, "rate_limit_error", True),
        (500, "server_error", True),
        (503, "server_error", True),
        (413, "context_limit_exceeded", False),
    ],
)
def test_sdk_status_errors_map_to_provider_errors(
    status_code: int,
    expected_type: str,
    retryable: bool,
) -> None:
    _events, error = run_stream(
        lambda _request: error_response(status_code, "provider exploded")
    )

    assert isinstance(error, ProviderError)
    assert error.error_type == expected_type
    assert error.status_code == status_code
    assert error.retryable is retryable
    assert error.provider_id == "openai"
    assert "secret" not in error.message


def test_sdk_context_limit_message_is_not_retryable() -> None:
    _events, error = run_stream(
        lambda _request: error_response(
            400,
            "This model has too many tokens in the prompt",
        )
    )

    assert isinstance(error, ProviderError)
    assert error.error_type == "context_limit_exceeded"
    assert error.retryable is False


def test_sdk_retry_after_header_is_forwarded_when_valid() -> None:
    _events, error = run_stream(
        lambda _request: error_response(429, "slow down", headers={"retry-after": "7"})
    )

    assert isinstance(error, ProviderError)
    assert error.retry_after_seconds == 7.0

    _events, invalid = run_stream(
        lambda _request: error_response(
            429,
            "slow down",
            headers={"retry-after": "not-a-number"},
        )
    )

    assert isinstance(invalid, ProviderError)
    assert invalid.retry_after_seconds is None


def test_sdk_connection_error_maps_to_network_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    _events, error = run_stream(fail)

    assert isinstance(error, ProviderError)
    assert error.error_type == "network_error"
    assert error.retryable is True


def test_sdk_timeout_maps_to_timeout_error() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    _events, error = run_stream(timeout)

    assert isinstance(error, ProviderError)
    assert error.error_type == "timeout_error"
    assert error.retryable is True


class _MidStreamFailure(httpx.AsyncByteStream):
    def __init__(self, first: bytes) -> None:
        self._first = first

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._first
        raise httpx.ReadError("connection dropped")

    async def aclose(self) -> None:
        return


def test_partial_output_is_visible_when_stream_fails_midway() -> None:
    first_chunk = f"data: {json.dumps(text_chunk('hello'))}\n\n".encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_MidStreamFailure(first_chunk),
        )

    events, error = run_stream(handler)

    assert [event.text for event in events if event.type == "content_delta"] == [
        "hello"
    ]
    assert isinstance(error, ProviderError)
    assert error.error_type == "network_error"
    assert error.retryable is True


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    async def aclose(self) -> None:
        self.closed = True


def test_cancelled_error_is_not_converted_and_stream_is_released() -> None:
    blocking_stream = _BlockingStream()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=blocking_stream,
        )

    config = resolved_config()
    sdk = async_sdk(config, handler)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)

    async def run() -> BaseException | None:
        async def consume() -> None:
            async for _event in client.stream(ContextSnapshot("", ())):
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return asyncio.CancelledError()
        finally:
            await sdk.close()
        return None

    result = asyncio.run(run())

    assert isinstance(result, asyncio.CancelledError)
    assert blocking_stream.closed is True


def test_adapter_does_not_secretly_retry_and_runner_owns_retries() -> None:
    recorder = Recorder(
        _SequenceHandler(
            [
                lambda _request: error_response(500, "server exploded"),
                lambda _request: sse_response(
                    [text_chunk("recovered", finish_reason="stop")]
                ),
            ]
        )
    )
    config = resolved_config()
    sdk = async_sdk(config, recorder)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)
    runner = ModelRetryRunner(
        policy=RetryPolicy(max_retries=2, jitter_ratio=0),
        sleep=_no_sleep,
    )
    decisions: list[Any] = []

    async def run() -> list[Any]:
        events: list[Any] = []
        async for event in runner.stream(
            lambda: client.stream(ContextSnapshot("", ())),
            on_retry=lambda _error, decision: decisions.append(decision),
        ):
            events.append(event)
        await sdk.close()
        return events

    events = asyncio.run(run())

    assert len(recorder.requests) == 2
    assert decisions and decisions[0].attempt == 1
    assert events[-1].type == "message_completed"
    assert events[-1].final_text == "recovered"


class _SequenceHandler:
    def __init__(self, handlers: list[Any]) -> None:
        self._handlers = list(handlers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if not self._handlers:
            raise AssertionError("unexpected extra provider request")
        return self._handlers.pop(0)(request)


async def _no_sleep(_seconds: float) -> None:
    return None
