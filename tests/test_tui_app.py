"""Integration tests for the thin OneCode TUI App with a fake controller."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from application.commands import CommandOutcome
from application.types import (
    CancelResult,
    DetailRef,
    DetailResult,
    InteractionRequest,
    InteractionRequested,
    ResponseResult,
    SnapshotUpdate,
    ToolUpdate,
)
from services.permissions import PermissionOption, PermissionResponse
from services.tools.types import ToolExecutionResult
from textual.app import App
from ui.tui.app import OneCodeTuiApp
from ui.tui.composer import Composer
from ui.tui.conversation.view import ConversationView
from ui.tui.modals import (
    CommandOutputModal,
    PermissionModal,
    PlanApprovalModal,
)

from tui_test_support import make_snapshot


class FakeRuntime:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.model = "fake-model"
        self.error_log_recorder = None
        self.plan_store = None
        self.state = None


class FakeController:
    def __init__(self, workspace: Path) -> None:
        self.runtime = FakeRuntime(workspace)
        self._updates: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False
        self.started = False
        self.submissions: list[str] = []
        self.commands: list[str] = []
        self.responses: list[tuple[str, str, Any]] = []
        self.details: list[DetailRef] = []
        self.cancels = 0
        self.withdrawn: list[str] = []
        self.resume_queue_calls = 0

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        self.started = True

    async def watch(self):
        yield SnapshotUpdate(snapshot=make_snapshot(session_id="s1"))
        while True:
            item = await self._updates.get()
            if item is None:
                return
            yield item

    def snapshot(self):
        return make_snapshot(session_id="s1")

    async def submit(self, text, *, attachments=(), kind="prompt"):
        from application.types import SubmissionReceipt

        self.submissions.append(text)
        return SubmissionReceipt(
            input_id="input-1", session_id="s1", status="started"
        )

    async def execute_command(self, line: str) -> CommandOutcome:
        self.commands.append(line)
        if line.startswith("/status"):
            return CommandOutcome(
                name="status",
                category="view",
                data={
                    "session_id": "s1",
                    "workspace": str(self.runtime.workspace),
                    "provider_label": "fake",
                    "model": "fake-model",
                    "configured": True,
                    "turn_count": 0,
                    "permission_mode": "default",
                    "plan_mode": False,
                },
            )
        if line.startswith("/exit"):
            self._closed = True
            return CommandOutcome(name="exit", category="lifecycle", action="exit")
        return CommandOutcome(name="", category="view", status="noop")

    async def respond(self, request_id, kind=None, payload=None) -> ResponseResult:
        self.responses.append((request_id, kind, payload))
        return ResponseResult(request_id=request_id, accepted=True)

    async def load_detail(self, ref: DetailRef) -> DetailResult:
        self.details.append(ref)
        return DetailResult(success=True, text="detail")

    async def close(self) -> None:
        self._closed = True

    async def cancel_active(self) -> CancelResult:
        self.cancels += 1
        return CancelResult(cancelled=True)

    async def withdraw(self, input_id: str):
        from application.types import WithdrawalResult

        self.withdrawn.append(input_id)
        return WithdrawalResult(input_id=input_id, withdrawn=True, text="queued text")

    async def resume_queue(self) -> None:
        self.resume_queue_calls += 1

    async def reload_model_config(self) -> bool:
        return True

    async def push(self, update: Any) -> None:
        await self._updates.put(update)


def _run(coro):
    return asyncio.run(coro)


def _build_app(tmp_path: Path) -> tuple[OneCodeTuiApp, FakeController]:
    controller = FakeController(tmp_path)
    app = OneCodeTuiApp(workspace=tmp_path, controller=controller)
    return app, controller


def test_app_starts_injected_controller_and_focuses_composer(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert controller.started is True
            assert app.query_one(Composer).has_focus
            assert app.query_one(ConversationView) is not None

    _run(scenario())


def test_submit_forwards_text_and_clears_composer(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "hello world"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert controller.submissions == ["hello world"]
            assert composer.text == ""

    _run(scenario())


def test_command_opens_and_closes_output_modal(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "/status"
            await pilot.pause()
            # The first Escape closes the command completion overlay.
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert controller.commands == ["/status"]
            assert any(
                isinstance(screen, CommandOutputModal) for screen in app.screen_stack
            )
            await pilot.press("escape")
            await pilot.pause()
            assert not any(
                isinstance(screen, CommandOutputModal) for screen in app.screen_stack
            )

    _run(scenario())


def test_exit_command_stops_app(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "/exit"
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert controller.closed is True

    _run(scenario())


def test_detail_request_is_routed_to_controller(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            ref = DetailRef(
                session_id="s1", kind="tool_result", identifier="t1", relative_path="x"
            )
            app.on_detail_requested(
                SimpleNamespace(detail_ref=ref)
            )
            await pilot.pause()
            assert controller.details == [ref]

    _run(scenario())


def test_cancel_without_active_run_does_not_delete_draft(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "draft"
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
            # Ctrl+C never exits and never silently deletes the draft.
            assert composer.text == "draft"
            assert controller.cancels == 0

    _run(scenario())


def test_command_completion_inserts_selected_command(tmp_path: Path) -> None:
    async def scenario() -> None:
        app, _controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "/sta"
            await pilot.pause()
            from ui.tui.completion import CompletionOverlay

            overlay = app.query_one(CompletionOverlay)
            assert overlay.display is True
            assert overlay.mode == "command"
            await pilot.press("tab")
            await pilot.pause()
            assert composer.text == "/status"

    _run(scenario())


def test_permission_interaction_opens_modal_and_responds(tmp_path: Path) -> None:
    request = SimpleNamespace(
        request_id="permission-1",
        session_id="s1",
        kind="permission",
        payload=SimpleNamespace(
            descriptor=SimpleNamespace(name="bash"),
            decision=SimpleNamespace(reason="needs", guard_policies=()),
            classification=SimpleNamespace(read_only=True),
            tool_input={"command": "ls"},
            options=(
                PermissionOption(
                    id="allow_once", label="allow once", action="allow", scope="once"
                ),
                PermissionOption(id="deny", label="deny", action="deny", scope="once"),
            ),
        ),
        run_id=None,
        options=(),
    )

    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await controller.push(InteractionRequested(generation=1, sequence=1, request=request))
            await pilot.pause()
            assert any(
                isinstance(screen, PermissionModal) for screen in app.screen_stack
            )
            await pilot.press("escape")
            await pilot.pause()
            assert controller.responses
            request_id, kind, payload = controller.responses[0]
            assert request_id == "permission-1"
            assert kind == "permission"
            assert isinstance(payload, PermissionResponse)
            assert payload.action == "deny"

    _run(scenario())


def test_startup_builds_runtime_and_rejects_unconfigured_prompt(
    tmp_path: Path,
) -> None:
    from services.model.types import ModelUsage

    calls: dict[str, Any] = {}

    class _Loop:
        async def stream(self, text, attachments=()):
            if False:  # pragma: no cover
                yield None

    def factory(workspace, *, trust_prompt=None):
        calls["workspace"] = workspace
        calls["trust_prompt"] = trust_prompt
        return SimpleNamespace(
            state=SimpleNamespace(
                session_id="s1",
                usage=ModelUsage(),
                metadata={},
                turn_count=0,
            ),
            message_store=None,
            loop=_Loop(),
            configured=False,
            permission_prompter=None,
            user_question_prompter=None,
            error_log_recorder=None,
            workspace=workspace,
            model="",
        )

    async def scenario() -> None:
        app = OneCodeTuiApp(workspace=tmp_path, runtime_factory=factory)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert calls["workspace"] == tmp_path
            composer = app.query_one(Composer)
            composer.focus()
            composer.text = "hello"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            # Unconfigured prompt is rejected and the draft is retained.
            assert composer.text == "hello"

    _run(scenario())


def test_withdraw_last_places_text_in_empty_composer(tmp_path: Path) -> None:
    from application.types import QueueChanged, QueueItem

    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await controller.push(
                QueueChanged(
                    generation=1,
                    sequence=1,
                    queue=(QueueItem(input_id="q1", text="queued text"),),
                    paused=False,
                )
            )
            await pilot.pause()
            await pilot.press("f8")
            await pilot.pause()
            assert controller.withdrawn == ["q1"]
            assert app.query_one(Composer).text == "queued text"

    _run(scenario())


def test_resume_queue_when_paused(tmp_path: Path) -> None:
    from application.types import QueueChanged

    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await controller.push(
                QueueChanged(generation=1, sequence=1, queue=(), paused=True)
            )
            await pilot.pause()
            await pilot.press("f9")
            await pilot.pause()
            assert controller.resume_queue_calls == 1

    _run(scenario())


def test_interaction_resolved_dismisses_open_modal(tmp_path: Path) -> None:
    request = SimpleNamespace(
        request_id="permission-2",
        session_id="s1",
        kind="permission",
        payload=SimpleNamespace(
            descriptor=SimpleNamespace(name="bash"),
            decision=SimpleNamespace(reason="needs", guard_policies=()),
            classification=SimpleNamespace(read_only=True),
            tool_input={"command": "ls"},
            options=(
                PermissionOption(
                    id="allow_once", label="allow once", action="allow", scope="once"
                ),
                PermissionOption(id="deny", label="deny", action="deny", scope="once"),
            ),
        ),
        run_id=None,
        options=(),
    )

    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await controller.push(
                InteractionRequested(generation=1, sequence=1, request=request)
            )
            await pilot.pause()
            assert any(
                isinstance(screen, PermissionModal) for screen in app.screen_stack
            )
            from application.types import InteractionResolved

            await controller.push(
                InteractionResolved(
                    generation=1, sequence=2, request_id="permission-2", outcome="cancelled"
                )
            )
            await pilot.pause()
            assert not any(
                isinstance(screen, PermissionModal) for screen in app.screen_stack
            )

    _run(scenario())


def test_plan_approval_tool_update_opens_modal(tmp_path: Path) -> None:
    result = ToolExecutionResult(
        tool_call_id="t1",
        tool_name="exit_plan_mode",
        content='{"summary": "done", "status": "awaiting_approval"}',
        metadata={"awaiting_approval": True, "plan_path": "/tmp/plan.md"},
    )
    update = ToolUpdate(
        generation=1,
        sequence=1,
        tool_call_id="t1",
        tool_name="exit_plan_mode",
        status="completed",
        result=result,
        assistant_call_id="c1",
        model_turn_index=0,
    )

    async def scenario() -> None:
        app, controller = _build_app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await controller.push(update)
            await pilot.pause()
            assert any(
                isinstance(screen, PlanApprovalModal) for screen in app.screen_stack
            )
            app.screen.query_one("#plan-reject").press()
            await pilot.pause()
            assert "/plan reject" in controller.commands

    _run(scenario())
