from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from services.model.stream import ModelStreamEvent
from services.tools.types import ToolCall


async def _fake_stream() -> AsyncIterator[ModelStreamEvent]:
    yield ModelStreamEvent.content_delta("hello")
    yield ModelStreamEvent.content_delta(" world")
    yield ModelStreamEvent.tool_call_completed(
        ToolCall(id="call-1", name="read_file", input={"file_path": "a.txt"})
    )
    yield ModelStreamEvent.message_completed(
        assistant_message={"role": "assistant", "content": "hello world"},
        final_text="hello world",
        tool_calls=(
            ToolCall(id="call-1", name="read_file", input={"file_path": "a.txt"}),
        ),
        stop_reason="tool_calls",
    )


def test_model_stream_events_are_async_iterable() -> None:
    async def run() -> list[ModelStreamEvent]:
        return [event async for event in _fake_stream()]

    events = asyncio.run(run())

    assert [event.type for event in events] == [
        "content_delta",
        "content_delta",
        "tool_call_completed",
        "message_completed",
    ]
    assert events[0].text == "hello"
    assert events[-1].metadata["tool_calls"] == (
        ToolCall(id="call-1", name="read_file", input={"file_path": "a.txt"}),
    )
