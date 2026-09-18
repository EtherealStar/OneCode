from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from application.session import SessionController
from application.types import (
    AssistantDelta,
    DetailRef,
    DetailResult,
    MessageCommitted,
    QueueChanged,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    SessionSnapshot,
    SnapshotUpdate,
    StatusChanged,
    ToolUpdate,
)
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.context.run_facts import InterruptedRunFacts
from services.context.transcript import InMemoryTranscriptStore
from services.observability import ErrorLogRecorder, TraceRecorder
from services.tools.types import ToolExecutionResult


class FakeLoop:
    """Controllable agent loop used to exercise the controller."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.attachments_seen: list[Any] = []
        self.running = 0
        self.max_running = 0
        self.script: Any = self._default_script
        self.facts: InterruptedRunFacts | None = None
        self.done = asyncio.Event()

    async def _default_script(self, prompt: str):
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(
            type="assistant_delta",
            text=f"reply:{prompt}",
            metadata={"assistant_call_id": "call", "model_turn_index": 1},
        )
        yield AgentEvent(
            type="assistant_message_completed",
            text=f"reply:{prompt}",
            metadata={"assistant_call_id": "call", "model_turn_index": 1},
        )
        yield AgentEvent(type="completed", text=f"reply:{prompt}")

    async def stream(self, prompt: str, *, attachments: Any = None):
        self.prompts.append(prompt)
        self.attachments_seen.append(attachments)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.done.clear()
        try:
            async for event in self.script(prompt):
                yield event
        finally:
            self.running -= 1
            self.done.set()

    def snapshot_run_facts(self, status: str | None = None) -> InterruptedRunFacts | None:
        return self.facts


def make_runtime(
    tmp_path: Path,
    loop: FakeLoop,
    *,
    configured: bool = True,
    store: MessageStore | None = None,
) -> SimpleNamespace:
    state = RuntimeState()
    message_store = store or MessageStore(
        transcript_store=InMemoryTranscriptStore(state.session_id)
    )
    return SimpleNamespace(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        configured=configured,
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
    )


async def _next_update(iterator: Any, timeout: float = 2.0) -> Any:
    return await asyncio.wait_for(iterator.__anext__(), timeout)


def test_watch_yields_snapshot_then_updates_without_gap(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            stream = controller.watch()
            first = await _next_update(stream)
            assert isinstance(first, SnapshotUpdate)
            assert isinstance(first.snapshot, SessionSnapshot)

            receipt = await controller.submit("hello")
            assert receipt.status == "started"

            seen: list[Any] = []
            while True:
                item = await _next_update(stream)
                seen.append(item)
                if isinstance(item, RunCompleted):
                    break
            kinds = [type(item).__name__ for item in seen]
            assert "RunStarted" in kinds
            assert "AssistantDelta" in kinds
            assert "MessageCommitted" in kinds
            assert "RunCompleted" in kinds
            await stream.aclose()

    asyncio.run(scenario())


def test_second_watch_recovers_running_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            yield AgentEvent(
                type="assistant_delta",
                text="partial",
                metadata={"assistant_call_id": "c1", "model_turn_index": 1},
            )
            await gate.wait()
            yield AgentEvent(type="completed", text="partial done")

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            await controller.submit("go")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            resumed = controller.watch()
            first = await _next_update(resumed)
            assert isinstance(first, SnapshotUpdate)
            assert first.snapshot.run.active is True
            assert first.snapshot.run.assistant_text == "partial"
            await resumed.aclose()

            gate.set()
            await asyncio.wait_for(loop.done.wait(), 2.0)

    asyncio.run(scenario())


def test_slow_subscriber_resyncs_with_complete_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()

        async def script(prompt: str):
            for index in range(300):
                yield AgentEvent(
                    type="assistant_delta",
                    text="x",
                    metadata={"assistant_call_id": "c1", "model_turn_index": 1},
                )
            yield AgentEvent(type="completed", text="x" * 300)

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            stream = controller.watch()
            await _next_update(stream)
            await controller.submit("go")
            await asyncio.wait_for(loop.done.wait(), 5.0)

            accumulated = 0
            saw_snapshot = False
            while True:
                try:
                    item = await asyncio.wait_for(stream.__anext__(), 0.2)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
                if isinstance(item, SnapshotUpdate) and item.snapshot.run.active:
                    saw_snapshot = True
                    accumulated = len(item.snapshot.run.assistant_text)
                elif isinstance(item, AssistantDelta):
                    accumulated += len(item.text)
            assert saw_snapshot is True
            assert accumulated == 300
            await stream.aclose()

    asyncio.run(scenario())


def test_subscriber_cannot_mutate_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            stream = controller.watch()
            first = await _next_update(stream)
            snapshot = first.snapshot
            with pytest.raises(dataclasses.FrozenInstanceError):
                snapshot.paused = True  # type: ignore[misc]
            await stream.aclose()

    asyncio.run(scenario())


def test_unsubscribe_does_not_cancel_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text="done")

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            stream = controller.watch()
            await _next_update(stream)
            await stream.aclose()
            await controller.submit("work")
            await asyncio.sleep(0)
            gate.set()
            await asyncio.wait_for(loop.done.wait(), 2.0)
            assert loop.prompts == ["work"]
            # A fresh subscription still sees the committed history/status.
            assert controller.snapshot().run.active is False

    asyncio.run(scenario())


def test_concurrent_submits_use_one_foreground_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()

        async def script(prompt: str):
            yield AgentEvent(
                type="assistant_delta",
                text=prompt,
                metadata={"assistant_call_id": "c", "model_turn_index": 1},
            )
            await asyncio.sleep(0)
            yield AgentEvent(type="completed", text=prompt)

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            receipts = await asyncio.gather(
                controller.submit("a"),
                controller.submit("b"),
                controller.submit("c"),
            )
            assert receipts[0].status == "started"
            assert [r.status for r in receipts[1:]] == ["queued", "queued"]
            await asyncio.wait_for(loop.done.wait(), 2.0)
            for _ in range(50):
                if len(loop.prompts) == 3:
                    break
                await asyncio.sleep(0.01)
            assert loop.prompts == ["a", "b", "c"]
            assert loop.max_running == 1

    asyncio.run(scenario())


def test_withdraw_requires_queued_item(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text="ok")

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            first = await controller.submit("running")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            queued = await controller.submit("pending")
            assert first.status == "started"
            assert queued.status == "queued"

            started = await controller.withdraw(first.input_id)
            assert started.withdrawn is False
            assert started.reason == "already_started"

            withdrawn = await controller.withdraw(queued.input_id)
            assert withdrawn.withdrawn is True
            assert withdrawn.text == "pending"
            missing = await controller.withdraw("missing")
            assert missing.withdrawn is False

            gate.set()
            await asyncio.wait_for(loop.done.wait(), 2.0)

    asyncio.run(scenario())


def test_cancel_pauses_queue_and_resume_drains(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            if prompt == "first":
                yield AgentEvent(
                    type="assistant_delta",
                    text="half",
                    metadata={"assistant_call_id": "c1", "model_turn_index": 1},
                )
                await gate.wait()
            yield AgentEvent(
                type="assistant_delta",
                text=prompt,
                metadata={"assistant_call_id": "c", "model_turn_index": 1},
            )
            yield AgentEvent(type="completed", text=prompt)

        loop.script = script
        state = RuntimeState()
        store = MessageStore(transcript_store=InMemoryTranscriptStore(state.session_id))
        loop.facts = InterruptedRunFacts(
            session_id=state.session_id,
            assistant_text="half",
            status="interrupted",
        )
        controller = SessionController(make_runtime(tmp_path, loop, store=store))
        async with controller:
            await controller.submit("first")
            queued = await controller.submit("second")
            assert queued.status == "queued"
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            result = await controller.cancel_active()
            assert result.cancelled is True
            assert controller.snapshot().paused is True
            assert [item.text for item in controller.snapshot().queue] == ["second"]

            await controller.resume_queue()
            assert controller.snapshot().paused is False
            for _ in range(50):
                if loop.prompts == ["first", "second"]:
                    break
                await asyncio.sleep(0.01)
            assert loop.prompts == ["first", "second"]

    asyncio.run(scenario())


def test_resume_queue_refused_after_cleanup_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text=prompt)

        loop.script = script
        state = RuntimeState()
        store = MessageStore(transcript_store=InMemoryTranscriptStore(state.session_id))

        def boom(facts: Any, *, error_log_recorder: Any = None) -> None:
            raise OSError("disk full")

        store.finalize_interrupted_run = boom  # type: ignore[method-assign]
        loop.facts = InterruptedRunFacts(
            session_id=state.session_id, status="interrupted"
        )
        controller = SessionController(make_runtime(tmp_path, loop, store=store))
        async with controller:
            await controller.submit("first")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            result = await controller.cancel_active()
            assert result.cleanup_success is False
            assert controller.snapshot().paused is True
            await controller.resume_queue()
            # A failed cleanup must not silently drain the queue.
            assert controller.snapshot().paused is True

    asyncio.run(scenario())


def test_cancel_wakes_pending_interactions(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = FakeLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text=prompt)

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            await controller.submit("first")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            waiter = asyncio.create_task(
                controller.request_interaction("permission", {"n": 1})
            )
            await asyncio.sleep(0)
            assert controller.active_interaction() is not None
            await controller.cancel_active()
            answer = await asyncio.wait_for(waiter, 2.0)
            assert answer.cancelled is True

    asyncio.run(scenario())


def test_failed_turn_pauses_queue_until_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()

        async def script(prompt: str):
            if prompt == "boom":
                raise RuntimeError("provider exploded")
            yield AgentEvent(type="completed", text=prompt)

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            await controller.submit("boom")
            await asyncio.wait_for(loop.done.wait(), 2.0)
            await asyncio.sleep(0)
            assert controller.snapshot().paused is True
            await controller.resume_queue()
            await controller.submit("after")
            await asyncio.sleep(0)
            for _ in range(50):
                if "after" in loop.prompts:
                    break
                await asyncio.sleep(0.01)
            assert loop.prompts == ["boom", "after"]

    asyncio.run(scenario())


def test_unconfigured_rejects_prompts_but_commands_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()
        controller = SessionController(
            make_runtime(tmp_path, loop, configured=False)
        )
        async with controller:
            receipt = await controller.submit("hi")
            assert receipt.status == "rejected"
            assert receipt.reason == "not_configured"
            outcome = await controller.execute_command("/status")
            assert outcome.status == "ok"
            assert outcome.data["configured"] is False

    asyncio.run(scenario())


def test_close_is_idempotent_and_flushes(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()
        runtime = make_runtime(tmp_path, loop)
        controller = SessionController(runtime)
        await controller.start()
        await controller.close()
        await controller.close()
        assert controller.closed is True
        # A submit after close is rejected rather than starting a turn.
        receipt = await controller.submit("late")
        assert receipt.status == "rejected"

    asyncio.run(scenario())


def test_load_detail_rejects_stale_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            ref = DetailRef(
                session_id="other-session",
                kind="tool_result",
                identifier="call-1",
                relative_path="tool-results/call-1.txt",
            )
            result = await controller.load_detail(ref)
            assert isinstance(result, DetailResult)
            assert result.success is False
            assert result.error == "stale_session"

    asyncio.run(scenario())


def test_tool_updates_are_published_in_arrival_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = FakeLoop()

        async def script(prompt: str):
            yield AgentEvent(
                type="tool_result",
                result=ToolExecutionResult(
                    tool_call_id="b",
                    tool_name="bash",
                    content="b done",
                ),
            )
            yield AgentEvent(
                type="tool_result",
                result=ToolExecutionResult(
                    tool_call_id="a",
                    tool_name="read_file",
                    content="a done",
                ),
            )
            yield AgentEvent(type="completed", text="ok")

        loop.script = script
        controller = SessionController(make_runtime(tmp_path, loop))
        async with controller:
            stream = controller.watch()
            await _next_update(stream)
            await controller.submit("run tools")
            await asyncio.wait_for(loop.done.wait(), 2.0)
            tool_ids: list[str] = []
            while True:
                try:
                    item = await asyncio.wait_for(stream.__anext__(), 0.2)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
                if isinstance(item, ToolUpdate):
                    tool_ids.append(item.tool_call_id)
            assert tool_ids == ["b", "a"]
            await stream.aclose()

    asyncio.run(scenario())
