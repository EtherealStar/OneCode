"""Tests for stable ``assistant_call_id`` and ``model_turn_index`` (execplan §M5).

这些测试是 execplan M5 的核心:它们证明 runtime 事件层为每次模型
调用生成的稳定 ID 会被所有派生事件(assistant delta、tool
declaration、tool result)正确继承,且下一轮模型调用获得新的 ID。

测试通过驱动 ``AgentLoop.stream()`` 的 ``ScriptedStreamingModel``
mock,逐条 ``AgentEvent`` 收集并断言:

- 同一次模型调用的 ``assistant_delta``、``tool_call_ready``、
  ``assistant_message_completed``、``tool_started``、
  ``tool_progress``、``tool_result`` 共享同一个
  ``assistant_call_id`` 和 ``model_turn_index``;
- 下一轮模型调用(因为工具结果而重新调用模型)使用新的 ID;
- ``mint_assistant_call_id`` 在同一 session 内生成稳定、可推断
  的字符串,作为测试断言的稳定事实来源。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from core.stream_events import (
    AgentEvent,
    mint_assistant_call_id,
)
from services.context.message_store import MessageStore
from services.model.retry import ModelRetryRunner
from services.model.stream import ModelStreamEvent
from services.tools.executor import ToolExecutionUpdate
from services.tools.types import ToolCall, ToolExecutionResult


def _assistant_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


class _ScriptedModel:
    """Replay a list of ``ModelStreamEvent``/exception scripts in order."""

    def __init__(self, scripts: list[list]) -> None:
        self._scripts = [list(s) for s in scripts]
        self.calls: list[None] = []

    async def stream(self, snapshot):  # type: ignore[no-untyped-def]
        self.calls.append(None)
        if not self._scripts:
            raise AssertionError("Scripted model received an unexpected call")
        for item in self._scripts.pop(0):
            if isinstance(item, BaseException):
                raise item
            yield item


class _ScriptedToolExecutor:
    """Replay tool execution events deterministically."""

    def __init__(self, events: list[ToolExecutionUpdate]) -> None:
        self._events = list(events)
        self.calls: list[tuple] = []

    async def execute(self, tool_calls, state):  # type: ignore[no-untyped-def]
        self.calls.append((tool_calls, state))
        for event in self._events:
            yield event


def _make_loop(
    state: RuntimeState,
    transcript_root: Path,
    model: _ScriptedModel,
    executor: _ScriptedToolExecutor,
) -> AgentLoop:
    message_store = MessageStore(
        transcript_root=transcript_root / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    return AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model,  # type: ignore[arg-type]
        tool_executor=executor,  # type: ignore[arg-type]
        model_retry_runner=ModelRetryRunner(),
    )


async def _collect(loop: AgentLoop, prompt: str) -> list[AgentEvent]:
    return [event async for event in loop.stream(prompt)]


def test_mint_assistant_call_id_is_stable_for_same_inputs() -> None:
    """Same session / turn / model turn index must produce same id."""

    a = mint_assistant_call_id("abcd1234-5678-90ab-cdef", 3, 1)
    b = mint_assistant_call_id("abcd1234-5678-90ab-cdef", 3, 1)
    assert a == b
    assert a.startswith("ac_")
    assert "t3" in a
    assert "m1" in a


def test_mint_assistant_call_id_varies_per_model_turn(tmp_path: Path) -> None:
    """Same turn, different model_turn_index must produce different ids."""

    base = mint_assistant_call_id("abcd1234-5678-90ab-cdef", 3, 1)
    later = mint_assistant_call_id("abcd1234-5678-90ab-cdef", 3, 2)
    assert base != later


def test_model_turn_events_share_assistant_call_id(tmp_path: Path) -> None:
    """assistant/tool events from one model call share the same id."""

    state = RuntimeState()
    tool_call = ToolCall(id="call-1", name="read_file", input={"path": "x.py"})
    # Two scripts: first turn declares a tool, second turn gives a
    # final answer. The test only cares about the *first* turn
    # events, so the loop can safely call the model twice.
    model = _ScriptedModel(
        [
            [
                ModelStreamEvent.content_delta("Let me read "),
                ModelStreamEvent.tool_call_completed(tool_call),
                ModelStreamEvent.message_completed(
                    assistant_message=_assistant_message("Let me read "),
                    final_text="Let me read ",
                    tool_calls=(tool_call,),
                ),
            ],
            [
                ModelStreamEvent.content_delta("done"),
                ModelStreamEvent.message_completed(
                    assistant_message=_assistant_message("done"),
                    final_text="done",
                ),
            ],
        ]
    )
    executor = _ScriptedToolExecutor(
        [
            ToolExecutionUpdate(
                type="started",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            ),
            ToolExecutionUpdate(
                type="result",
                result=ToolExecutionResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content="ok",
                ),
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            ),
        ]
    )
    loop = _make_loop(state, tmp_path, model, executor)

    events = asyncio.run(_collect(loop, "do it"))

    attributed_types = {
        "assistant_delta",
        "tool_call_ready",
        "assistant_message_completed",
        "tool_started",
        "tool_result",
    }
    attributed = [e for e in events if e.type in attributed_types]
    assert attributed, "expected attributed events"
    # The first turn's events come before the second turn's
    # ``assistant_message_completed``; the latter is the only
    # ``assistant_message_completed`` carrying a different
    # ``assistant_call_id``. Filter to the first turn.
    first_completion_idx = next(
        i for i, e in enumerate(events) if e.type == "assistant_message_completed"
    )
    first_turn = [
        e
        for e in attributed
        if events.index(e) <= first_completion_idx
    ]
    distinct_call_ids = {
        event.metadata.get("assistant_call_id") for event in first_turn
    }
    assert len(distinct_call_ids) == 1, distinct_call_ids
    distinct_turn_indices = {
        event.metadata.get("model_turn_index") for event in first_turn
    }
    assert len(distinct_turn_indices) == 1, distinct_turn_indices


def test_next_model_turn_gets_new_assistant_call_id(tmp_path: Path) -> None:
    """A second model call (after tool result) must use a new id."""

    state = RuntimeState()
    tool_call = ToolCall(id="call-1", name="read_file", input={"path": "x.py"})
    model = _ScriptedModel(
        [
            [
                ModelStreamEvent.content_delta("first "),
                ModelStreamEvent.tool_call_completed(tool_call),
                ModelStreamEvent.message_completed(
                    assistant_message=_assistant_message("first "),
                    final_text="first ",
                    tool_calls=(tool_call,),
                ),
            ],
            [
                ModelStreamEvent.content_delta("done"),
                ModelStreamEvent.message_completed(
                    assistant_message=_assistant_message("done"),
                    final_text="done",
                ),
            ],
        ]
    )
    executor = _ScriptedToolExecutor(
        [
            ToolExecutionUpdate(
                type="started",
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            ),
            ToolExecutionUpdate(
                type="result",
                result=ToolExecutionResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content="ok",
                ),
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            ),
        ]
    )
    loop = _make_loop(state, tmp_path, model, executor)

    events = asyncio.run(_collect(loop, "do it"))

    completions = [e for e in events if e.type == "assistant_message_completed"]
    assert len(completions) == 2
    first_completion, second_completion = completions
    first_id = first_completion.metadata.get("assistant_call_id")
    second_id = second_completion.metadata.get("assistant_call_id")
    assert first_id and second_id
    assert first_id != second_id
    assert (
        first_completion.metadata.get("model_turn_index")
        < second_completion.metadata.get("model_turn_index")
    )
    # First turn's tool events share the first completion's id; the
    # second turn's assistant_delta also shares the second id.
    first_turn_tool_events = [
        e
        for e in events
        if e.type in {"tool_call_ready", "tool_started", "tool_result"}
    ]
    for e in first_turn_tool_events:
        assert e.metadata.get("assistant_call_id") == first_id
    second_turn_assistant = [
        e
        for e in events
        if e.type == "assistant_delta" and events.index(e) > events.index(first_completion)
    ]
    for e in second_turn_assistant:
        assert e.metadata.get("assistant_call_id") == second_id
