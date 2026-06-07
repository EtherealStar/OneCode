from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from core.transitions import TransitionReason
from services.attachments.context_preparer import AttachmentContextPreparer
from services.attachments.types import AttachmentMessage
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.retry import ModelRetryRunner, RetryPolicy
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
class ScriptedStreamingModel:
    scripts: list[list[ModelStreamEvent | BaseException]]
    snapshots: list[ContextSnapshot] = field(default_factory=list)

    async def stream(self, snapshot: ContextSnapshot):
        self.snapshots.append(snapshot)
        if not self.scripts:
            raise AssertionError("Scripted model received an unexpected call")
        script = self.scripts.pop(0)
        for item in script:
            if isinstance(item, BaseException):
                raise item
            yield item


@dataclass
class FakeReactiveCompactor:
    calls: int = 0

    async def reactive_compact(self, state: RuntimeState, *, error: ProviderError):
        self.calls += 1
        state.metadata["reactive_compacted"] = error.error_type


@dataclass
class FollowupToolExecutor:
    attachment: dict[str, Any]

    async def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ):
        del state
        for tool_call in tool_calls:
            yield ToolExecutionUpdate(
                type="result",
                result=ToolExecutionResult(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content="Launching skill: code-review",
                    followup_messages=(self.attachment,),
                ),
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
            )


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


def collect_events(loop: AgentLoop, prompt: str) -> list[AgentEvent]:
    async def run() -> list[AgentEvent]:
        return [event async for event in loop.stream(prompt)]

    return asyncio.run(run())


async def noop_sleep(_seconds: float) -> None:
    return None


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


def test_loop_appends_successful_tool_followup_attachments(tmp_path: Path) -> None:
    tool_call = ToolCall(id="call-1", name="skill", input={"skill": "code-review"})
    attachment = AttachmentMessage(
        attachment={
            "type": "skill",
            "skill_name": "code-review",
            "content": "Follow this review checklist.",
            "source": "project",
        },
        attachment_id="skill_att",
        source="skill_tool",
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
                assistant_message={"role": "assistant", "content": []},
                final_text="",
                tool_calls=(tool_call,),
            ),
            LLMResponse(
                assistant_message=assistant_message("final"),
                final_text="final",
            ),
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
        tool_executor=FollowupToolExecutor(attachment),
    )

    result = run_to_final_text(loop, "use the skill")

    assert result == "final"
    stored = message_store.current_messages()
    assert [message["role"] for message in stored[:4]] == [
        "user",
        "assistant",
        "tool_result",
        "attachment",
    ]
    assert stored[3]["attachment"]["type"] == "skill"
    assert all(
        message.get("role") != "attachment"
        for message in model_client.snapshots[1].messages
    )
    assert "[skill loaded: code-review]" in model_client.snapshots[1].messages[-1]["content"]


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


def test_loop_retries_retryable_provider_error_and_hides_partial_delta(
    tmp_path: Path,
) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = ScriptedStreamingModel(
        [
            [
                ModelStreamEvent.content_delta("partial"),
                ProviderError(
                    "rate limited",
                    error_type="rate_limit_error",
                    status_code=429,
                    retryable=True,
                ),
            ],
            [
                ModelStreamEvent.content_delta("final"),
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("final"),
                    final_text="final",
                ),
            ],
        ]
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=FakeToolExecutor(),
        model_retry_runner=ModelRetryRunner(
            policy=RetryPolicy(max_retries=1, jitter_ratio=0),
            sleep=noop_sleep,
        ),
    )

    events = collect_events(loop, "recover")

    assert [event.text for event in events if event.type == "assistant_delta"] == [
        "final"
    ]
    assert "partial" not in json.dumps(message_store.current_messages())
    assert any(
        event.type == "transition"
        and event.transition == TransitionReason.RATE_LIMIT_RETRY.value
        for event in events
    )
    assert events[-1].type == "completed"
    assert events[-1].text == "final"
    assert len(model_client.snapshots) == 2


def test_loop_escalates_max_output_tokens_before_persisting_truncated_output(
    tmp_path: Path,
) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = ScriptedStreamingModel(
        [
            [
                ModelStreamEvent.content_delta("cut"),
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut"),
                    final_text="cut",
                    stop_reason="length",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.content_delta("complete"),
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("complete"),
                    final_text="complete",
                ),
            ],
        ]
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=FakeToolExecutor(),
    )

    events = collect_events(loop, "long answer")

    assert [event.text for event in events if event.type == "assistant_delta"] == [
        "complete"
    ]
    assert any(
        event.type == "transition"
        and event.transition == TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE.value
        for event in events
    )
    assert state.has_escalated_max_output_tokens is True
    assert model_client.snapshots[1].usage_hints["request_overrides"] == {
        "max_output_tokens": 64000
    }
    assert [message["role"] for message in message_store.current_messages()] == [
        "user",
        "assistant",
    ]
    assert "cut" not in json.dumps(message_store.current_messages())
    assert events[-1].text == "complete"


def test_loop_continues_after_repeated_max_output_interruption(
    tmp_path: Path,
) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = ScriptedStreamingModel(
        [
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-1"),
                    final_text="cut-1",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-2"),
                    final_text="cut-2",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("done"),
                    final_text="done",
                ),
            ],
        ]
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=FakeToolExecutor(),
    )

    events = collect_events(loop, "long answer")
    messages = message_store.current_messages()

    assert state.max_output_recovery_count == 1
    assert any(
        event.type == "transition"
        and event.transition == TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY.value
        for event in events
    )
    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[1] == assistant_message("cut-2")
    assert "Output token limit hit. Resume directly" in messages[2]["content"]
    assert messages[-1] == assistant_message("done")
    assert events[-1].text == "done"


def test_loop_stops_max_output_recovery_after_three_continuations(
    tmp_path: Path,
) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    model_client = ScriptedStreamingModel(
        [
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-1"),
                    final_text="cut-1",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-2"),
                    final_text="cut-2",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-3"),
                    final_text="cut-3",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("cut-4"),
                    final_text="cut-4",
                    output_interrupted=True,
                ),
            ],
            [
                ModelStreamEvent.content_delta("partial-final"),
                ModelStreamEvent.message_completed(
                    assistant_message=assistant_message("partial-final"),
                    final_text="partial-final",
                    output_interrupted=True,
                ),
            ],
        ]
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,  # type: ignore[arg-type]
        tool_executor=FakeToolExecutor(),
    )

    events = collect_events(loop, "long answer")

    assert state.max_output_recovery_count == 3
    assert events[-1].type == "completed"
    assert events[-1].text == "partial-final"
    assert [event.text for event in events if event.type == "assistant_delta"] == [
        "partial-final"
    ]
    assert message_store.current_messages()[-1] == assistant_message("partial-final")
