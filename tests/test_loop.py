from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from core.transitions import TransitionReason
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.types import LLMResponse, ModelUsage
from services.tools.types import ToolCall, ToolExecutionResult


@dataclass
class FakeModelClient:
    responses: list[LLMResponse]
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    def send(self, snapshot: ContextSnapshot) -> LLMResponse:
        self.snapshots.append(snapshot)
        if not self.responses:
            raise AssertionError("Fake model received an unexpected call")
        return self.responses.pop(0)


@dataclass
class FakeToolExecutor:
    calls: list[tuple[tuple[ToolCall, ...], RuntimeState]] = field(default_factory=list)

    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ) -> list[ToolExecutionResult]:
        self.calls.append((tool_calls, state))
        return [
            ToolExecutionResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"result for {tool_call.name}",
            )
            for tool_call in tool_calls
        ]


def make_loop(
    responses: list[LLMResponse],
    *,
    transcript_root: Path,
    max_turns: int = 20,
) -> tuple[AgentLoop, MessageStore, FakeModelClient, FakeToolExecutor]:
    state = RuntimeState(max_turns=max_turns)
    message_store = MessageStore(
        transcript_root=transcript_root,
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    context_engine = ContextEngine(message_store)
    model_client = FakeModelClient(responses)
    tool_executor = FakeToolExecutor()
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=model_client,
        tool_executor=tool_executor,
    )
    return loop, message_store, model_client, tool_executor


def assistant_message(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def test_loop_stops_without_tool_calls(tmp_path: Path) -> None:
    loop, message_store, model_client, tool_executor = make_loop(
        [
            LLMResponse(
                assistant_message=assistant_message("done"),
                final_text="done",
                usage=ModelUsage(input_tokens=3, output_tokens=5),
            )
        ],
        transcript_root=tmp_path / ".onecode",
    )

    result = loop.run("hello")

    assert result == "done"
    assert loop.state.last_transition == TransitionReason.COMPLETED
    assert loop.state.turn_count == 1
    assert loop.state.usage.input_tokens == 3
    assert loop.state.usage.output_tokens == 5
    assert len(model_client.snapshots) == 1
    assert tool_executor.calls == []
    assert message_store.current_messages()[-1] == assistant_message("done")


def test_loop_continues_when_tool_calls_present(tmp_path: Path) -> None:
    tool_call = ToolCall(id="call-1", name="read_file", input={"path": "a.txt"})
    loop, message_store, model_client, tool_executor = make_loop(
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(tool_call,),
            ),
            LLMResponse(
                assistant_message=assistant_message("final"),
                final_text="final",
            ),
        ],
        transcript_root=tmp_path / ".onecode",
    )

    result = loop.run("inspect")

    assert result == "final"
    assert loop.state.last_transition == TransitionReason.COMPLETED
    assert loop.state.turn_count == 2
    assert len(model_client.snapshots) == 2
    assert len(tool_executor.calls) == 1
    assert tool_executor.calls[0][0] == (tool_call,)

    messages = message_store.current_messages()
    assert messages[2] == {
        "role": "tool_result",
        "tool_call_id": "call-1",
        "tool_name": "read_file",
        "content": "result for read_file",
        "is_error": False,
        "metadata": {},
    }
    assert model_client.snapshots[1].messages == messages[:3]


def test_loop_uses_tool_calls_not_stop_reason(tmp_path: Path) -> None:
    tool_call = ToolCall(id="call-1", name="search", input={"query": "x"})
    loop, _message_store, model_client, tool_executor = make_loop(
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(tool_call,),
                stop_reason=None,
            ),
            LLMResponse(
                assistant_message=assistant_message("stopped despite stop_reason"),
                final_text="stopped despite stop_reason",
                stop_reason="tool_use",
            ),
        ],
        transcript_root=tmp_path / ".onecode",
    )

    result = loop.run("go")

    assert result == "stopped despite stop_reason"
    assert len(model_client.snapshots) == 2
    assert len(tool_executor.calls) == 1
    assert loop.state.last_transition == TransitionReason.COMPLETED


def test_loop_max_turns(tmp_path: Path) -> None:
    tool_call = ToolCall(id="call-1", name="loop", input={})
    loop, _message_store, model_client, tool_executor = make_loop(
        [
            LLMResponse(
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(tool_call,),
            )
        ],
        transcript_root=tmp_path / ".onecode",
        max_turns=1,
    )

    result = loop.run("keep going")

    assert result == "Stopped: maximum turn count reached."
    assert loop.state.last_transition == TransitionReason.MAX_TURNS
    assert loop.state.turn_count == 2
    assert len(model_client.snapshots) == 1
    assert len(tool_executor.calls) == 1
