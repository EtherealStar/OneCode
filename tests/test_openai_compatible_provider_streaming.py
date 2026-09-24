"""通过真实 SDK 传输驱动的 Chat Completions 适配器流式测试。

适配器消费 OpenAI SDK 的类型化流，因此这些测试把 SSE 传输体注入
`httpx.MockTransport`，再观察 OneCode 的 `ModelStreamEvent`。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sdk_test_support import (
    SSE_HEADERS,
    Recorder,
    async_sdk,
    resolved_config,
    sse_body,
    text_chunk,
    tool_call_chunk,
    usage_chunk,
)

from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
)
from services.context.snapshot import ContextSnapshot
from services.model.types import ProviderError
from services.tools.types import ToolCall


def collect_events(
    chunks: list[dict[str, Any]],
    *,
    snapshot: ContextSnapshot | None = None,
    config: Any | None = None,
) -> tuple[list[Any], Recorder]:
    config = config or resolved_config()
    recorder = Recorder(
        lambda _request: httpx.Response(
            200, content=sse_body(chunks), headers=SSE_HEADERS
        )
    )
    sdk = async_sdk(config, recorder)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)

    async def run() -> list[Any]:
        events = [
            event async for event in client.stream(snapshot or ContextSnapshot("", ()))
        ]
        await sdk.close()
        return events

    return asyncio.run(run()), recorder


def test_chat_completions_streams_text_deltas_and_final_message() -> None:
    events, recorder = collect_events(
        [
            text_chunk("hello"),
            text_chunk(" world", finish_reason="stop"),
            usage_chunk(prompt_tokens=2, completion_tokens=3),
        ]
    )

    assert [event.text for event in events if event.type == "content_delta"] == [
        "hello",
        " world",
    ]
    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.final_text == "hello world"
    assert completed.stop_reason == "stop"
    assert completed.usage is not None
    assert completed.usage.input_tokens == 2
    assert completed.usage.output_tokens == 3
    assert recorder.last_body["stream"] is True


def test_chat_completions_marks_length_finish_as_output_interrupted() -> None:
    events, _recorder = collect_events([text_chunk("cut", finish_reason="length")])

    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.stop_reason == "length"
    assert completed.output_interrupted is True


def test_chat_completions_stream_accumulates_tool_call_arguments() -> None:
    events, _recorder = collect_events(
        [
            tool_call_chunk(
                0,
                call_id="call_x",
                name="read_",
                arguments='{"file_',
            ),
            tool_call_chunk(
                0,
                name="file",
                arguments='path":"a.txt"}',
                finish_reason="tool_calls",
            ),
        ]
    )
    tool_completed = next(
        event for event in events if event.type == "tool_call_completed"
    )

    assert tool_completed.tool_call == ToolCall(
        id="call_x",
        name="read_file",
        input={"file_path": "a.txt"},
    )
    assert events[-1].metadata["tool_calls"] == (tool_completed.tool_call,)


def test_chat_completions_merges_interleaved_tool_call_fragments() -> None:
    events, _recorder = collect_events(
        [
            tool_call_chunk(
                0, call_id="call_a", name="read_file", arguments='{"file_path":'
            ),
            tool_call_chunk(1, call_id="call_b", name="grep", arguments='{"pattern":'),
            tool_call_chunk(0, arguments='"a.txt"}'),
            tool_call_chunk(1, arguments='"needle"}', finish_reason="tool_calls"),
        ]
    )

    completed = events[-1]
    assert completed.metadata["tool_calls"] == (
        ToolCall(id="call_a", name="read_file", input={"file_path": "a.txt"}),
        ToolCall(id="call_b", name="grep", input={"pattern": "needle"}),
    )


def test_chat_completions_rejects_invalid_tool_arguments() -> None:
    recorder = Recorder(
        lambda _request: httpx.Response(
            200,
            content=sse_body(
                [tool_call_chunk(0, call_id="call_x", name="read_file", arguments="[]")]
            ),
            headers=SSE_HEADERS,
        )
    )
    config = resolved_config()
    sdk = async_sdk(config, recorder)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)

    async def run() -> None:
        async for _event in client.stream(ContextSnapshot("", ())):
            pass

    with pytest.raises(ProviderError) as exc_info:
        try:
            asyncio.run(run())
        finally:
            asyncio.run(sdk.close())

    assert exc_info.value.error_type == "invalid_tool_arguments"


def test_chat_completions_stream_ends_without_usage_chunk() -> None:
    events, _recorder = collect_events([text_chunk("ok", finish_reason="stop")])

    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.usage is None


class _RecordingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes) -> None:
        self._content = content
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._content

    async def aclose(self) -> None:
        self.closed = True


def test_chat_completions_closes_sdk_stream_on_early_aclose() -> None:
    recording_stream = _RecordingAsyncStream(
        sse_body([text_chunk("a"), text_chunk("b", finish_reason="stop")]).encode()
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=SSE_HEADERS, stream=recording_stream)

    config = resolved_config()
    sdk = async_sdk(config, handler)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)

    async def run() -> None:
        generator = client.stream(ContextSnapshot("", ()))
        first = await generator.__anext__()
        assert first.type == "content_delta"
        await generator.aclose()
        await sdk.close()

    asyncio.run(run())

    assert recording_stream.closed is True
