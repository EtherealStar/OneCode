from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from core.transitions import TransitionReason
from services.attachments.context_preparer import AttachmentContextPreparer
from services.attachments.types import AttachmentMessage
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import LLMResponse, ModelUsage
from services.model.types import ProviderError
from services.observability import JsonlTraceSink, TraceRecorder
from services.tools.executor import ToolExecutionUpdate
from services.tools.types import ToolCall, ToolExecutionResult


@dataclass
class FakeModelClient:
    responses: list[LLMResponse]
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(self, snapshot: ContextSnapshot):
        self.snapshots.append(snapshot)
        if not self.responses:
            raise AssertionError("Fake model received an unexpected call")
        response = self.responses.pop(0)
        yield ModelStreamEvent.message_completed(
            assistant_message=response.assistant_message,
            final_text=response.final_text,
            tool_calls=response.tool_calls,
            stop_reason=response.stop_reason,
            usage=response.usage,
            output_interrupted=response.output_interrupted,
        )


@dataclass
class FakeToolExecutor:
    calls: list[tuple[tuple[ToolCall, ...], RuntimeState]] = field(default_factory=list)

    async def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ):
        self.calls.append((tool_calls, state))
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


@dataclass
class ContextLimitThenSuccessModel:
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(self, snapshot: ContextSnapshot):
        self.snapshots.append(snapshot)
        if len(self.snapshots) == 1:
            raise ProviderError(
                "too many tokens",
                error_type="context_limit_exceeded",
                status_code=413,
            )
        yield ModelStreamEvent.message_completed(
            assistant_message=assistant_message("recovered"),
            final_text="recovered",
        )


@dataclass
class FakeReactiveCompactor:
    calls: int = 0

    async def reactive_compact(self, state: RuntimeState, *, error: ProviderError):
        self.calls += 1
        state.metadata["reactive_compacted"] = error.error_type


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


def run_to_final_text(
    loop: AgentLoop,
    prompt: str,
    *,
    attachments: object = None,
) -> str:
    async def run() -> str:
        final_text = ""
        kwargs = {} if attachments is None else {"attachments": attachments}
        async for event in loop.stream(prompt, **kwargs):
            if event.type == "completed":
                final_text = event.text
        return final_text

    return asyncio.run(run())


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

    result = run_to_final_text(loop, "hello")

    assert result == "done"
    assert loop.state.last_transition == TransitionReason.COMPLETED
    assert loop.state.turn_count == 1
    assert loop.state.usage.input_tokens == 3
    assert loop.state.usage.output_tokens == 5
    assert len(model_client.snapshots) == 1
    assert tool_executor.calls == []
    assert message_store.current_messages()[-1] == assistant_message("done")


def test_loop_persists_attachment_but_model_sees_projection(tmp_path: Path) -> None:
    attachment = AttachmentMessage(
        attachment={
            "type": "file",
            "path": "note.txt",
            "content": "1\tone",
            "offset": 1,
            "limit": 1,
        },
        attachment_id="att_loop",
        source="user_input",
    ).to_message()
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = FakeModelClient(
        [
            LLMResponse(
                assistant_message=assistant_message("done"),
                final_text="done",
            )
        ]
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(
            message_store,
            context_preparer=AttachmentContextPreparer(),
        ),
        model_client=model_client,
        tool_executor=FakeToolExecutor(),
    )

    result = run_to_final_text(loop, "summarize @note.txt", attachments=[attachment])

    assert result == "done"
    stored = message_store.current_messages()
    assert stored[0]["role"] == "user"
    assert stored[1]["role"] == "attachment"
    snapshot_roles = [message["role"] for message in model_client.snapshots[0].messages]
    assert snapshot_roles == ["user", "assistant", "tool_result"]


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

    result = run_to_final_text(loop, "inspect")

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

    result = run_to_final_text(loop, "go")

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

    result = run_to_final_text(loop, "keep going")

    assert result == "Stopped: maximum turn count reached."
    assert loop.state.last_transition == TransitionReason.MAX_TURNS
    assert loop.state.turn_count == 2
    assert len(model_client.snapshots) == 1
    assert len(tool_executor.calls) == 1


def test_loop_records_interaction_model_and_transition_trace(tmp_path: Path) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    context_engine = ContextEngine(message_store)
    model_client = FakeModelClient(
        [
            LLMResponse(
                assistant_message=assistant_message("done"),
                final_text="done",
                usage=ModelUsage(input_tokens=3, output_tokens=5),
            )
        ]
    )
    tool_executor = FakeToolExecutor()
    sink = JsonlTraceSink(
        tmp_path / ".onecode",
        state.session_id,
        flush_interval_seconds=60,
    )
    recorder = TraceRecorder(
        session_id=state.session_id,
        workspace=tmp_path,
        sink=sink,
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=model_client,
        tool_executor=tool_executor,
        trace_recorder=recorder,
    )

    assert run_to_final_text(loop, "hello") == "done"
    recorder.flush()

    records = [
        json.loads(line)
        for line in sink.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    names = [record["name"] for record in records]
    assert "interaction" in names
    assert "context_prepare" in names
    assert "model_call" in names
    assert "transition" in names
    model_end = next(
        record
        for record in records
        if record["name"] == "model_call" and record["record_type"] == "span_end"
    )
    assert model_end["attributes"]["input_tokens"] == 3
    assert model_end["attributes"]["output_tokens"] == 5
    assert "hello" not in json.dumps(records, ensure_ascii=False)


def test_loop_reactive_compacts_once_after_context_limit(tmp_path: Path) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = ContextLimitThenSuccessModel()
    compactor = FakeReactiveCompactor()
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=FakeToolExecutor(),
        compaction_service=compactor,
    )

    result = run_to_final_text(loop, "large context")

    assert result == "recovered"
    assert compactor.calls == 1
    assert state.has_attempted_reactive_compact is True
    assert state.metadata["reactive_compacted"] == "context_limit_exceeded"
    assert len(model_client.snapshots) == 2
