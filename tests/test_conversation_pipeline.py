"""End-to-end projection tests against a real SessionController.

The fake runtime persists messages through the same store contract the real
loop uses, so the projection's live tree can be compared with the controller's
authoritative history after compact, cancel, resync, and normal completion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application.session import SessionController
from application.types import (
    RunCompleted,
    SnapshotUpdate,
    ToolUpdate,
)
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.context.run_facts import InterruptedRunFacts, ToolCallFact
from services.context.transcript import InMemoryTranscriptStore
from services.observability import ErrorLogRecorder, TraceRecorder
from services.tools.types import ToolCall, ToolExecutionResult
from ui.tui.projection import ConversationProjection


def tree(projection: ConversationProjection) -> list[tuple[Any, ...]]:
    return [
        (
            message.message_id,
            message.role,
            tuple(
                (
                    part.kind,
                    part.content,
                    part.tool_call_id,
                    part.tool_name,
                    tuple(sorted(part.tool_input.items())),
                    part.status,
                    part.result_preview,
                    part.is_error,
                )
                for part in message.parts
            ),
        )
        for message in projection.messages
    ]


class RecordingLoop:
    """Controllable loop that also writes through the message store."""

    def __init__(self, store: MessageStore) -> None:
        self.store = store
        self.prompts: list[str] = []
        self.script: Any = self._default_script
        self.facts: InterruptedRunFacts | None = None
        self.done = asyncio.Event()
        self.gate = asyncio.Event()

    async def _default_script(self, loop: RecordingLoop, prompt: str):
        loop.store.append_user(prompt)
        yield AgentEvent(
            type="interaction_started",
            metadata={"user_message_uuid": loop.store.last_record_uuid or ""},
        )
        loop.store.append_assistant(
            {"role": "assistant", "content": f"reply:{prompt}"},
            assistant_call_id="c1",
            model_turn_index=1,
        )
        yield AgentEvent(
            type="assistant_delta",
            text=f"reply:{prompt}",
            metadata={"assistant_call_id": "c1", "model_turn_index": 1},
        )
        yield AgentEvent(
            type="assistant_message_completed",
            text=f"reply:{prompt}",
            metadata={"assistant_call_id": "c1", "model_turn_index": 1},
        )
        yield AgentEvent(type="completed", text=f"reply:{prompt}")

    async def stream(self, prompt: str, *, attachments: Any = None):
        self.prompts.append(prompt)
        self.done.clear()
        try:
            async for event in self.script(self, prompt):
                yield event
        finally:
            self.done.set()

    def snapshot_run_facts(
        self, status: str | None = None
    ) -> InterruptedRunFacts | None:
        return self.facts


def make_runtime(tmp_path: Path, loop: RecordingLoop) -> SimpleNamespace:
    message_store = loop.store
    state = RuntimeState(session_id=message_store.transcript_store.session_id)
    return SimpleNamespace(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        configured=True,
        loop=loop,
        provider_label="Fake",
        model="fake-model",
        error_log_recorder=ErrorLogRecorder.noop(),
        trace_recorder=TraceRecorder.noop(),
        attachment_collector=None,
        plan_store=None,
        permission_prompter=None,
        user_question_prompter=None,
        mcp_manager=None,
        compaction_service=None,
    )


async def _next(stream: Any, timeout: float = 2.0) -> Any:
    return await asyncio.wait_for(stream.__anext__(), timeout)


async def pump_until(
    stream: Any,
    projection: ConversationProjection,
    stop: Any,
    timeout: float = 3.0,
) -> Any:
    while True:
        update = await _next(stream, timeout)
        if isinstance(update, SnapshotUpdate):
            projection.replace(update.snapshot)
        else:
            projection.apply(update)
        if stop(update):
            return update


async def start_watch(
    controller: SessionController,
) -> tuple[Any, ConversationProjection]:
    stream = controller.watch()
    first = await _next(stream)
    assert isinstance(first, SnapshotUpdate)
    projection = ConversationProjection(first.snapshot)
    return stream, projection


def test_text_turn_live_matches_authoritative_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = RecordingLoop(
            MessageStore(transcript_store=InMemoryTranscriptStore("session-pipe-text"))
        )
        runtime = make_runtime(tmp_path, loop)
        controller = SessionController(runtime)
        async with controller:
            stream, projection = await start_watch(controller)
            await controller.submit("hello")
            await pump_until(stream, projection, lambda u: isinstance(u, RunCompleted))

            authoritative = ConversationProjection(controller.snapshot())
            assert tree(projection) == tree(authoritative)
            await stream.aclose()

    asyncio.run(scenario())


def test_tool_turn_live_matches_authoritative_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = RecordingLoop(
            MessageStore(transcript_store=InMemoryTranscriptStore("session-pipe-tool"))
        )

        async def script(loop: RecordingLoop, prompt: str):
            loop.store.append_user(prompt)
            yield AgentEvent(
                type="interaction_started",
                metadata={"user_message_uuid": loop.store.last_record_uuid or ""},
            )
            loop.store.append_assistant(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "A", "name": "read_file", "input": {"path": "a.txt"}},
                        {"id": "B", "name": "bash", "input": {"command": "ls"}},
                    ],
                },
                assistant_call_id="c1",
                model_turn_index=1,
            )
            yield AgentEvent(
                type="assistant_message_completed",
                text="",
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            yield AgentEvent(
                type="tool_call_ready",
                metadata={
                    "assistant_call_id": "c1",
                    "model_turn_index": 1,
                    "tool_call": ToolCall(
                        id="A", name="read_file", input={"path": "a.txt"}
                    ),
                },
            )
            yield AgentEvent(
                type="tool_call_ready",
                metadata={
                    "assistant_call_id": "c1",
                    "model_turn_index": 1,
                    "tool_call": ToolCall(id="B", name="bash", input={"command": "ls"}),
                },
            )
            loop.store.append_tool_results(
                [
                    ToolExecutionResult(
                        tool_call_id="B", tool_name="bash", content="b done"
                    ),
                    ToolExecutionResult(
                        tool_call_id="A", tool_name="read_file", content="a done"
                    ),
                ],
                assistant_call_id="c1",
                model_turn_index=1,
            )
            yield AgentEvent(
                type="tool_result",
                result=ToolExecutionResult(
                    tool_call_id="B", tool_name="bash", content="b done"
                ),
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            yield AgentEvent(
                type="tool_result",
                result=ToolExecutionResult(
                    tool_call_id="A", tool_name="read_file", content="a done"
                ),
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            yield AgentEvent(type="completed", text="")

        loop.script = script
        runtime = make_runtime(tmp_path, loop)
        controller = SessionController(runtime)
        async with controller:
            stream, projection = await start_watch(controller)
            await controller.submit("run tools")
            await pump_until(stream, projection, lambda u: isinstance(u, RunCompleted))

            authoritative = ConversationProjection(controller.snapshot())
            assert tree(projection) == tree(authoritative)
            assistant = projection.messages[-1]
            tool_ids = [p.tool_call_id for p in assistant.parts if p.kind == "tool"]
            assert tool_ids == ["A", "B"]
            await stream.aclose()

    asyncio.run(scenario())


def test_cancel_cleanup_publishes_corrected_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = RecordingLoop(
            MessageStore(
                transcript_store=InMemoryTranscriptStore("session-pipe-cancel")
            )
        )

        async def script(loop: RecordingLoop, prompt: str):
            loop.store.append_user(prompt)
            user_uuid = loop.store.last_record_uuid
            yield AgentEvent(
                type="interaction_started",
                metadata={"user_message_uuid": user_uuid or ""},
            )
            yield AgentEvent(
                type="assistant_delta",
                text="half",
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            loop.store.append_assistant(
                {
                    "role": "assistant",
                    "content": "half",
                    "tool_calls": [
                        {"id": "A", "name": "bash", "input": {"command": "ls"}}
                    ],
                },
                assistant_call_id="c1",
                model_turn_index=1,
            )
            assistant_uuid = loop.store.last_record_uuid
            yield AgentEvent(
                type="assistant_message_completed",
                text="half",
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            yield AgentEvent(
                type="tool_call_ready",
                metadata={
                    "assistant_call_id": "c1",
                    "model_turn_index": 1,
                    "tool_call": ToolCall(id="A", name="bash", input={"command": "ls"}),
                },
            )
            yield AgentEvent(
                type="tool_started",
                metadata={
                    "assistant_call_id": "c1",
                    "model_turn_index": 1,
                    "tool_call_id": "A",
                    "tool_name": "bash",
                },
            )
            loop.facts = InterruptedRunFacts(
                session_id=loop.store.transcript_store.session_id,
                user_prompt_uuid=user_uuid,
                assistant_call_id="c1",
                model_turn_index=1,
                assistant_text="half",
                assistant_message={
                    "role": "assistant",
                    "content": "half",
                    "tool_calls": [
                        {"id": "A", "name": "bash", "input": {"command": "ls"}}
                    ],
                },
                assistant_record_uuid=assistant_uuid,
                tool_calls=(ToolCallFact(tool_call_id="A", tool_name="bash"),),
                status="interrupted",
            )
            await loop.gate.wait()
            yield AgentEvent(type="completed", text="half")

        loop.script = script
        runtime = make_runtime(tmp_path, loop)
        controller = SessionController(runtime)
        async with controller:
            stream, projection = await start_watch(controller)
            await controller.submit("go")
            await pump_until(stream, projection, lambda u: isinstance(u, ToolUpdate))
            assert any(
                part.kind == "tool"
                for message in projection.messages
                for part in message.parts
            )

            result = await controller.cancel_active()
            assert result.cancelled is True
            await pump_until(
                stream, projection, lambda u: isinstance(u, SnapshotUpdate)
            )

            authoritative = ConversationProjection(controller.snapshot())
            assert tree(projection) == tree(authoritative)
            # The unpaired declaration was corrected away; the half text stayed.
            assistant = projection.messages[-1]
            assert [part.kind for part in assistant.parts] == ["text"]
            assert assistant.parts[0].content == "half"
            await stream.aclose()

    asyncio.run(scenario())


class _FakeCompaction:
    def __init__(self, runtime: SimpleNamespace) -> None:
        self.runtime = runtime

    async def manual_compact(self, state: Any, *, focus: str | None = None) -> Any:
        store = self.runtime.message_store
        records = store.active_records()
        if records:
            last = records[-1]
            store.replace_messages_for_compaction(
                [
                    {
                        "role": "user",
                        "content": "[Compact boundary]",
                        "metadata": {"is_compact_boundary": True},
                    },
                    {
                        "role": "user",
                        "content": "Summary",
                        "metadata": {"is_compact_summary": True},
                    },
                    last.message,
                ],
                reason="manual",
                metadata={"boundary_id": "b1"},
                source_uuids=[None, None, last.uuid],
            )
        store.flush_transcript()
        return SimpleNamespace(
            trigger=None,
            token_before=100,
            token_after=10,
            messages=store.current_messages(),
        )


def test_compact_publishes_snapshot_and_preserves_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = RecordingLoop(
            MessageStore(
                transcript_store=InMemoryTranscriptStore("session-pipe-compact")
            )
        )
        runtime = make_runtime(tmp_path, loop)
        loop.store = runtime.message_store
        runtime.message_store.append_user("q1")
        runtime.message_store.append_assistant({"role": "assistant", "content": "a1"})
        runtime.message_store.flush_transcript()
        runtime.compaction_service = _FakeCompaction(runtime)

        controller = SessionController(runtime)
        async with controller:
            stream, projection = await start_watch(controller)
            before = tree(projection)

            await controller.execute_command("/compact")
            await pump_until(
                stream, projection, lambda u: isinstance(u, SnapshotUpdate)
            )

            authoritative = ConversationProjection(controller.snapshot())
            assert tree(projection) == tree(authoritative)
            assert tree(projection) == before
            await stream.aclose()

    asyncio.run(scenario())


def test_slow_subscriber_resync_converges_to_authoritative_history(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        loop = RecordingLoop(
            MessageStore(transcript_store=InMemoryTranscriptStore("session-pipe-slow"))
        )

        async def script(loop: RecordingLoop, prompt: str):
            loop.store.append_user(prompt)
            yield AgentEvent(
                type="interaction_started",
                metadata={"user_message_uuid": loop.store.last_record_uuid or ""},
            )
            for _ in range(300):
                yield AgentEvent(
                    type="assistant_delta",
                    text="x",
                    metadata={"assistant_call_id": "c1", "model_turn_index": 1},
                )
            loop.store.append_assistant(
                {"role": "assistant", "content": "x" * 300},
                assistant_call_id="c1",
                model_turn_index=1,
            )
            yield AgentEvent(
                type="assistant_message_completed",
                text="x" * 300,
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            yield AgentEvent(type="completed", text="x" * 300)

        loop.script = script
        runtime = make_runtime(tmp_path, loop)
        controller = SessionController(runtime)
        async with controller:
            stream, projection = await start_watch(controller)
            await controller.submit("go")
            await asyncio.wait_for(loop.done.wait(), 5.0)
            # Drain whatever the subscriber queue still holds.
            while True:
                try:
                    update = await asyncio.wait_for(stream.__anext__(), 0.2)
                except (TimeoutError, StopAsyncIteration):
                    break
                if isinstance(update, SnapshotUpdate):
                    projection.replace(update.snapshot)
                else:
                    projection.apply(update)

            authoritative = ConversationProjection(controller.snapshot())
            assert tree(projection) == tree(authoritative)
            await stream.aclose()

    asyncio.run(scenario())
