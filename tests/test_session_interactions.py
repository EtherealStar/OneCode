from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application.interactions import (
    InteractionCoordinator,
    PermissionPromptAdapter,
    UserQuestionPromptAdapter,
)
from application.session import SessionController
from application.types import (
    InteractionRequested,
    InteractionResolved,
    SnapshotUpdate,
)
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.context.transcript import InMemoryTranscriptStore
from services.observability import ErrorLogRecorder, TraceRecorder
from services.permissions.types import PermissionResponse


class IdleLoop:
    async def stream(self, prompt: str, *, attachments: Any = None):
        yield AgentEvent(type="completed", text="ok")

    def snapshot_run_facts(self, status: str | None = None) -> None:
        return None


def make_runtime(tmp_path: Path) -> SimpleNamespace:
    state = RuntimeState()
    return SimpleNamespace(
        workspace=tmp_path,
        state=state,
        message_store=MessageStore(
            transcript_store=InMemoryTranscriptStore(state.session_id)
        ),
        configured=True,
        loop=IdleLoop(),
        provider_label="Fake",
        model="fake",
        error_log_recorder=ErrorLogRecorder.noop(),
        trace_recorder=TraceRecorder.noop(),
        attachment_collector=None,
        plan_store=None,
        permission_prompter=None,
        user_question_prompter=None,
        mcp_manager=None,
    )


def test_only_one_request_is_active_and_others_wait() -> None:
    async def scenario() -> None:
        coordinator = InteractionCoordinator(session_id="s1")
        first = asyncio.create_task(coordinator.request("permission", {"n": 1}))
        second = asyncio.create_task(coordinator.request("question", {"n": 2}))
        await asyncio.sleep(0)
        active = coordinator.active_request()
        assert active is not None
        assert active.kind == "permission"
        assert len(coordinator.pending_requests()) == 2

        result = await coordinator.resolve(active.request_id, "permission", "ok-1")
        assert result.accepted is True
        await asyncio.sleep(0)
        active2 = coordinator.active_request()
        assert active2 is not None
        assert active2.kind == "question"
        await coordinator.resolve(active2.request_id, "question", "ok-2")

        assert (await first).payload == "ok-1"
        assert (await second).payload == "ok-2"

    asyncio.run(scenario())


def test_mismatched_and_expired_answers_are_rejected() -> None:
    async def scenario() -> None:
        coordinator = InteractionCoordinator(session_id="s1")
        task = asyncio.create_task(coordinator.request("permission", {"n": 1}))
        await asyncio.sleep(0)
        active = coordinator.active_request()
        assert active is not None

        wrong_kind = await coordinator.resolve(active.request_id, "question", "x")
        assert wrong_kind.accepted is False
        assert wrong_kind.reason == "request_type_mismatch"

        unknown = await coordinator.resolve("missing-id", "permission", "x")
        assert unknown.accepted is False
        assert unknown.reason == "expired_or_unknown"

        ok = await coordinator.resolve(active.request_id, "permission", "yes")
        assert ok.accepted is True
        answer = await task
        assert answer.payload == "yes"

    asyncio.run(scenario())


def test_cancel_all_wakes_every_waiter() -> None:
    async def scenario() -> None:
        coordinator = InteractionCoordinator(session_id="s1")
        first = asyncio.create_task(coordinator.request("permission", 1))
        second = asyncio.create_task(coordinator.request("question", 2))
        await asyncio.sleep(0)
        await coordinator.cancel_all()
        assert (await first).cancelled is True
        assert (await second).cancelled is True
        assert coordinator.active_request() is None

    asyncio.run(scenario())


def test_permission_adapter_denies_on_cancel() -> None:
    async def scenario() -> None:
        coordinator = InteractionCoordinator(session_id="s1")
        adapter = PermissionPromptAdapter(coordinator)
        request = SimpleNamespace(
            tool_call=SimpleNamespace(id="call-1"),
            options=(),
        )
        task = asyncio.create_task(adapter.request_permission(request))
        await asyncio.sleep(0)
        await coordinator.cancel_all()
        response = await task
        assert isinstance(response, PermissionResponse)
        assert response.action == "deny"

    asyncio.run(scenario())


def test_question_adapter_declines_on_cancel() -> None:
    async def scenario() -> None:
        coordinator = InteractionCoordinator(session_id="s1")
        adapter = UserQuestionPromptAdapter(coordinator)
        task = asyncio.create_task(adapter.ask_questions(()))
        await asyncio.sleep(0)
        await coordinator.cancel_all()
        response = await task
        assert response.declined is True

    asyncio.run(scenario())


def test_startup_trust_request_is_visible_in_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = SessionController(make_runtime(tmp_path))
        async with controller:
            stream = controller.watch()
            await stream.__anext__()
            trust_task = asyncio.create_task(
                controller.request_interaction(
                    "mcp_trust", {"server": "docs"}, options=("trust", "skip")
                )
            )
            await asyncio.sleep(0)
            snapshot = controller.snapshot()
            assert snapshot.interaction is not None
            assert snapshot.interaction.kind == "mcp_trust"
            assert snapshot.interaction.options == ("trust", "skip")

            result = await controller.respond(
                snapshot.interaction.request_id, "mcp_trust", "trust"
            )
            assert result.accepted is True
            answer = await trust_task
            assert answer.payload == "trust"

            seen_request = False
            seen_resolved = False
            while True:
                try:
                    item = await asyncio.wait_for(stream.__anext__(), 0.2)
                except (asyncio.TimeoutError, StopAsyncIteration):
                    break
                if isinstance(item, InteractionRequested):
                    seen_request = True
                if isinstance(item, InteractionResolved):
                    seen_resolved = True
            assert seen_request is True
            assert seen_resolved is True
            await stream.aclose()

    asyncio.run(scenario())


def test_respond_rejects_wrong_kind(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = SessionController(make_runtime(tmp_path))
        async with controller:
            task = asyncio.create_task(
                controller.request_interaction("permission", {"n": 1})
            )
            await asyncio.sleep(0)
            request = controller.active_interaction()
            assert request is not None
            rejected = await controller.respond(request.request_id, "question", "x")
            assert rejected.accepted is False
            accepted = await controller.respond(request.request_id, "permission", "ok")
            assert accepted.accepted is True
            await task

    asyncio.run(scenario())


def test_close_wakes_pending_interactions(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = SessionController(make_runtime(tmp_path))
        await controller.start()
        task = asyncio.create_task(
            controller.request_interaction("permission", {"n": 1})
        )
        await asyncio.sleep(0)
        await controller.close()
        answer = await asyncio.wait_for(task, 2.0)
        assert answer.cancelled is True

    asyncio.run(scenario())
