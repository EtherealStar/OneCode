from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application import commands as command_module
from application.session import SessionController
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.context.transcript import InMemoryTranscriptStore
from services.observability import ErrorLogRecorder, TraceRecorder
from services.plans.store import PlanStore


class IdleLoop:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.script: Any = None

    async def stream(self, prompt: str, *, attachments: Any = None):
        self.prompts.append(prompt)
        if self.script is not None:
            async for event in self.script(prompt):
                yield event
            return
        yield AgentEvent(type="completed", text=f"ok:{prompt}")

    def snapshot_run_facts(self, status: str | None = None) -> None:
        return None


class FakeProjectStore:
    def __init__(self) -> None:
        self.updates: list[Any] = []
        self.rules: tuple[str, ...] = ("allow:Bash(ls)",)

    def load_rules(self) -> tuple[str, ...]:
        return self.rules

    def apply_update(self, update: Any) -> None:
        self.updates.append(update)


class FakePermissionPolicy:
    def __init__(self) -> None:
        self.project_store = FakeProjectStore()


class FakeCompaction:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def manual_compact(self, state: Any, *, focus: Any = None) -> Any:
        self.calls.append(focus)
        return SimpleNamespace(
            trigger=SimpleNamespace(value="manual"),
            token_before=100,
            token_after=20,
            messages=[1, 2, 3],
        )


class FakeRuntime:
    def __init__(self, tmp_path: Path, loop: IdleLoop) -> None:
        self.workspace = tmp_path
        self.state = RuntimeState()
        self.message_store = MessageStore(
            transcript_store=InMemoryTranscriptStore(self.state.session_id)
        )
        self.configured = True
        self.loop = loop
        self.provider_label = "Fake"
        self.model = "fake"
        self.error_log_recorder = ErrorLogRecorder.noop()
        self.trace_recorder = TraceRecorder.noop()
        self.attachment_collector = None
        self.plan_store = PlanStore(tmp_path)
        self.permission_prompter = None
        self.user_question_prompter = None
        self.permission_policy = FakePermissionPolicy()
        self.compaction_service = FakeCompaction()
        self.task_store = None
        self.background_task_manager = None
        self.mcp_manager = None
        self.skill_provider = None
        self.session_memory_store = None
        self.long_term_memory_store = None

    def with_session(
        self,
        *,
        state: Any,
        message_store: Any,
        file_state_cache: Any = None,
    ) -> "FakeRuntime":
        clone = copy.copy(self)
        clone.state = state
        clone.message_store = message_store
        return clone


def make_controller(tmp_path: Path, loop: IdleLoop | None = None) -> SessionController:
    return SessionController(FakeRuntime(tmp_path, loop or IdleLoop()))


def test_registry_has_every_command_and_alias() -> None:
    specs = command_module.spec_by_name()
    for name in (
        "status",
        "usage",
        "memory",
        "permissions",
        "skills",
        "tasks",
        "mcp",
        "compact",
        "plan",
        "resume",
        "connect",
        "clear",
        "exit",
    ):
        assert name in specs
    assert specs["continue"].name == "resume"


def test_view_command_does_not_wait_for_running_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = IdleLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text="done")

        loop.script = script
        controller = make_controller(tmp_path, loop)
        async with controller:
            await controller.submit("block")
            await asyncio.sleep(0)
            assert controller.snapshot().run.active is True

            for command in ("/status", "/permissions", "/plan show"):
                outcome = await asyncio.wait_for(
                    controller.execute_command(command), 0.5
                )
                assert outcome.status == "ok"
            status = await controller.execute_command("/status")
            assert status.data["session_id"] == controller.session_id

            gate.set()
            await asyncio.sleep(0)

    asyncio.run(scenario())


def test_mutation_waits_for_safe_point(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = asyncio.Event()
        loop = IdleLoop()

        async def script(prompt: str):
            await gate.wait()
            yield AgentEvent(type="completed", text="done")

        loop.script = script
        controller = make_controller(tmp_path, loop)
        async with controller:
            await controller.submit("block")
            await asyncio.sleep(0)
            mutation = asyncio.create_task(
                controller.execute_command("/permissions add allow Bash(git status)")
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert mutation.done() is False

            gate.set()
            outcome = await asyncio.wait_for(mutation, 2.0)
            assert outcome.status == "ok"
            project_store = controller.runtime.permission_policy.project_store
            assert len(project_store.updates) == 1

    asyncio.run(scenario())


def test_permissions_show_returns_rules(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            outcome = await controller.execute_command("/permissions")
            assert outcome.category == "view"
            assert outcome.data["rules"] == ["allow:Bash(ls)"]

    asyncio.run(scenario())


def test_permissions_rejects_bad_usage(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            outcome = await controller.execute_command("/permissions add allow")
            assert outcome.status == "rejected"
            assert outcome.error

    asyncio.run(scenario())


def test_compact_calls_service(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            outcome = await controller.execute_command("/compact focus on tests")
            assert outcome.status == "ok"
            assert outcome.data["token_before"] == 100
            assert controller.runtime.compaction_service.calls == ["focus on tests"]

    asyncio.run(scenario())


def test_plan_enter_show_and_approve(tmp_path: Path) -> None:
    async def scenario() -> None:
        loop = IdleLoop()
        controller = make_controller(tmp_path, loop)
        async with controller:
            outcome = await controller.execute_command("/plan build a thing")
            assert outcome.status == "ok"
            assert controller.runtime.state.is_plan_mode() is True
            for _ in range(20):
                if loop.prompts == ["build a thing"]:
                    break
                await asyncio.sleep(0.01)
            assert loop.prompts == ["build a thing"]

            shown = await controller.execute_command("/plan show")
            assert shown.category == "view"
            assert shown.data["path"]

            approved = await controller.execute_command("/plan approve")
            assert approved.status == "ok"
            assert controller.runtime.state.is_plan_mode() is False

    asyncio.run(scenario())


def test_plan_reject_outside_plan_mode_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            outcome = await controller.execute_command("/plan reject")
            assert outcome.status == "rejected"

    asyncio.run(scenario())


def test_resume_dispatch_selector_and_target(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            selector = await command_module.dispatch(controller, "/resume")
            assert selector.action == "resume_selector"
            targeted = await command_module.dispatch(controller, "/resume session-1")
            assert targeted.action == "resume"
            assert targeted.data["target"] == "session-1"

    asyncio.run(scenario())


def test_unknown_command_is_reported(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            outcome = await controller.execute_command("/nope")
            assert outcome.status == "unknown"

    asyncio.run(scenario())


def test_clear_starts_new_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        async with controller:
            old_session = controller.session_id
            old_generation = controller.generation
            outcome = await controller.execute_command("/clear")
            assert outcome.action == "clear"
            assert controller.session_id != old_session
            assert controller.generation == old_generation + 1

    asyncio.run(scenario())


def test_exit_closes_controller(tmp_path: Path) -> None:
    async def scenario() -> None:
        controller = make_controller(tmp_path)
        await controller.start()
        await controller.execute_command("/exit")
        assert controller.closed is True

    asyncio.run(scenario())
