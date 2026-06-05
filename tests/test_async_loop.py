from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.tools.executor import ToolExecutionUpdate
from services.tools.types import ToolCall, ToolExecutionResult


@dataclass
class StreamingFakeModelClient:
    streams: list[list[ModelStreamEvent]]
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(
        self,
        snapshot: ContextSnapshot,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.snapshots.append(snapshot)
        if not self.streams:
            raise AssertionError("unexpected model stream")
        for event in self.streams.pop(0):
            await asyncio.sleep(0)
            yield event


@dataclass
class FakeToolExecutor:
    calls: list[tuple[ToolCall, ...]] = field(default_factory=list)

    async def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ):
        self.calls.append(tool_calls)
        for tool_call in tool_calls:
            yield ToolExecutionUpdate(
                type="result",
                result=ToolExecutionResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"result for {tool_call.name}",
                ),
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )


def make_loop(
    tmp_path: Path,
    streams: list[list[ModelStreamEvent]],
) -> tuple[AgentLoop, MessageStore, StreamingFakeModelClient, FakeToolExecutor]:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = StreamingFakeModelClient(streams)
    tool_executor = FakeToolExecutor()
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=tool_executor,
    )
    return loop, message_store, model_client, tool_executor


def test_async_loop_yields_deltas_before_completion(tmp_path: Path) -> None:
    loop, message_store, _model_client, _tool_executor = make_loop(
        tmp_path,
        [
            [
                ModelStreamEvent.content_delta("hello"),
                ModelStreamEvent.content_delta(" world"),
                ModelStreamEvent.message_completed(
                    assistant_message={"role": "assistant", "content": "hello world"},
                    final_text="hello world",
                ),
            ]
        ],
    )

    async def run() -> list:
        return [event async for event in loop.stream("say hello")]

    events = asyncio.run(run())

    assert [event.type for event in events[:3]] == [
        "interaction_started",
        "assistant_delta",
        "assistant_delta",
    ]
    assert events[-1].type == "completed"
    assert events[-1].text == "hello world"
    assert message_store.current_messages()[-1] == {
        "role": "assistant",
        "content": "hello world",
    }


def test_async_loop_continues_after_streamed_tool_call(tmp_path: Path) -> None:
    tool_call = ToolCall(id="call-1", name="read_file", input={"file_path": "a.txt"})
    loop, message_store, model_client, tool_executor = make_loop(
        tmp_path,
        [
            [
                ModelStreamEvent.tool_call_completed(tool_call),
                ModelStreamEvent.message_completed(
                    assistant_message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"file_path":"a.txt"}',
                                },
                            }
                        ],
                    },
                    final_text="",
                    tool_calls=(tool_call,),
                    stop_reason="tool_calls",
                ),
            ],
            [
                ModelStreamEvent.content_delta("final"),
                ModelStreamEvent.message_completed(
                    assistant_message={"role": "assistant", "content": "final"},
                    final_text="final",
                ),
            ],
        ],
    )

    async def run() -> list:
        return [event async for event in loop.stream("inspect")]

    events = asyncio.run(run())

    assert tool_executor.calls == [(tool_call,)]
    assert len(model_client.snapshots) == 2
    assert any(event.type == "tool_result" for event in events)
    messages = message_store.current_messages()
    assert messages[2]["role"] == "tool_result"
    assert model_client.snapshots[1].messages == messages[:3]
