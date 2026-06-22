"""Plan-mode unit tests: store, transitions, permission policy, and tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.runtime_state import PermissionMode, RuntimeState
from services.permissions import PermissionPolicy
from services.permissions.types import PermissionDecision
from services.plans import (
    PlanStore,
    build_plan_attachments_for_state,
    enter_plan_mode,
    exit_plan_mode,
)
from services.plans.attachments import (
    build_plan_mode_attachment,
    build_plan_mode_exit_attachment,
    build_plan_mode_reentry_attachment,
)
from services.plans.prompts import (
    render_plan_mode_exit,
    render_plan_mode_intro,
    render_plan_mode_reentry,
)
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolTarget,
)


# ---------------------------------------------------------------------------
# PlanStore
# ---------------------------------------------------------------------------


def test_plan_store_creates_layout(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    plan_dir = store.ensure_layout()
    assert plan_dir == tmp_path / ".onecode" / "plans"
    assert plan_dir.is_dir()
    # Idempotent.
    assert store.ensure_layout() == plan_dir


def test_plan_store_allocates_session_slug(tmp_path: Path) -> None:
    state = RuntimeState()
    store = PlanStore(tmp_path)
    plan_file = store.get_or_create_plan(state)
    assert plan_file.path == tmp_path / ".onecode" / "plans" / f"{state.session_id}.md"
    assert state.plan.plan_slug == state.session_id


def test_plan_store_reuses_existing_slug(tmp_path: Path) -> None:
    state = RuntimeState()
    state.plan.plan_slug = "demo"
    store = PlanStore(tmp_path)
    plan_file = store.get_or_create_plan(state)
    assert plan_file.path == tmp_path / ".onecode" / "plans" / "demo.md"


def test_plan_store_fork_creates_independent_copy(tmp_path: Path) -> None:
    state = RuntimeState()
    state.plan.plan_slug = "source"
    store = PlanStore(tmp_path)
    source_path = tmp_path / ".onecode" / "plans" / "source.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Source plan", encoding="utf-8")

    new_state = RuntimeState()
    forked = store.copy_for_fork(state, new_state)
    assert forked.path != source_path
    assert new_state.plan.plan_slug == forked.slug
    assert forked.read() == "# Source plan"


def test_plan_store_resume_recovery_returns_none_when_missing(tmp_path: Path) -> None:
    store = PlanStore(tmp_path)
    state = RuntimeState()
    assert store.recover_for_resume(state, "missing") is None


def test_plan_store_resume_recovery_uses_existing_file(tmp_path: Path) -> None:
    plans_dir = tmp_path / ".onecode" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / "old.md").write_text("# Old plan", encoding="utf-8")
    store = PlanStore(tmp_path)
    state = RuntimeState()
    recovered = store.recover_for_resume(state, "old")
    assert recovered is not None
    assert recovered.read() == "# Old plan"
    assert state.plan.plan_slug == "old"


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_enter_plan_mode_is_idempotent(tmp_path: Path) -> None:
    state = RuntimeState()
    store = PlanStore(tmp_path)
    enter_plan_mode(state, store)
    first_slug = state.plan.plan_slug
    pre_mode = state.plan.pre_plan_mode
    enter_plan_mode(state, store)
    assert state.plan.plan_slug == first_slug
    assert state.plan.pre_plan_mode == pre_mode
    assert state.permission_mode == PermissionMode.PLAN
    assert state.plan.needs_plan_mode_attachment is True


def test_exit_plan_mode_approved_restores_pre_mode(tmp_path: Path) -> None:
    state = RuntimeState()
    state.permission_mode = PermissionMode.DEFAULT
    store = PlanStore(tmp_path)
    enter_plan_mode(state, store)
    assert state.permission_mode == PermissionMode.PLAN
    transition = exit_plan_mode(state, store, approved=True)
    assert state.permission_mode == PermissionMode.DEFAULT
    assert transition.pre_plan_mode == PermissionMode.DEFAULT
    assert state.plan.has_exited_plan_mode is True
    assert state.plan.needs_plan_mode_exit_attachment is True


def test_exit_plan_mode_rejected_keeps_plan_mode(tmp_path: Path) -> None:
    state = RuntimeState()
    store = PlanStore(tmp_path)
    enter_plan_mode(state, store)
    exit_plan_mode(state, store, approved=False)
    assert state.permission_mode == PermissionMode.PLAN
    assert state.plan.has_exited_plan_mode is False


def test_exit_plan_mode_without_enter_raises(tmp_path: Path) -> None:
    state = RuntimeState()
    store = PlanStore(tmp_path)
    with pytest.raises(ValueError):
        exit_plan_mode(state, store, approved=True)


# ---------------------------------------------------------------------------
# Permission policy
# ---------------------------------------------------------------------------


def _policy() -> PermissionPolicy:
    return PermissionPolicy()


def _state(tmp_path: Path, *, plan_slug: str | None = "demo") -> RuntimeState:
    state = RuntimeState()
    state.metadata["workspace"] = str(tmp_path)
    state.plan.plan_slug = plan_slug
    state.permission_mode = PermissionMode.PLAN
    return state


def _descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description="test",
        input_schema={"type": "object"},
        handler=lambda *_a, **_kw: None,
    )


def _read_only_call(name: str, file_path: str) -> tuple[ToolCall, ToolCallClassification]:
    return (
        ToolCall(id="c1", name=name, input={"file_path": file_path}),
        ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(ToolTarget(kind="file", operation="read", value=file_path),),
        ),
    )


def test_plan_mode_denies_bash_when_not_read_only(tmp_path: Path) -> None:
    state = _state(tmp_path)
    descriptor = _descriptor("bash")
    classification = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(ToolTarget(kind="command", operation="execute", value="touch x"),),
    )
    decision = _policy().evaluate(
        tool_call=ToolCall(id="b1", name="bash", input={"command": "touch x"}),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=state,
    )
    assert decision.action == "deny"
    assert decision.source == "plan_mode"


def test_plan_mode_allows_read_only_bash(tmp_path: Path) -> None:
    state = _state(tmp_path)
    descriptor = _descriptor("bash")
    classification = ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(
            ToolTarget(kind="command", operation="execute", value="git status"),
        ),
    )
    decision = _policy().evaluate(
        tool_call=ToolCall(id="b1", name="bash", input={"command": "git status"}),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=state,
    )
    assert decision.action == "allow"


def test_plan_mode_denies_write_outside_plan_file(tmp_path: Path) -> None:
    state = _state(tmp_path, plan_slug="demo")
    descriptor = _descriptor("write_file")
    classification = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(
            ToolTarget(kind="file", operation="write", value=str(tmp_path / "src" / "x.py")),
        ),
    )
    decision = _policy().evaluate(
        tool_call=ToolCall(id="w1", name="write_file", input={"file_path": str(tmp_path / "src" / "x.py")}),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=state,
    )
    assert decision.action == "deny"
    assert decision.source == "plan_mode"


def test_plan_mode_allows_write_to_active_plan_file(tmp_path: Path) -> None:
    state = _state(tmp_path, plan_slug="demo")
    plan_path = tmp_path / ".onecode" / "plans" / "demo.md"
    descriptor = _descriptor("write_file")
    classification = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=False,
        targets=(
            ToolTarget(
                kind="file",
                operation="write",
                value=str(plan_path),
                normalized_value=str(plan_path),
            ),
        ),
    )
    decision = _policy().evaluate(
        tool_call=ToolCall(id="w1", name="write_file", input={"file_path": str(plan_path)}),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=state,
    )
    assert decision.action == "allow"


def test_plan_mode_hides_implementation_tools() -> None:
    state = RuntimeState()
    state.permission_mode = PermissionMode.PLAN
    policy = _policy()
    for hidden in ("task_create", "task_update", "skill"):
        assert policy.is_tool_visible(_descriptor(hidden), state) is False
    for allowed in ("read_file", "ask_user_question", "enter_plan_mode", "exit_plan_mode"):
        assert policy.is_tool_visible(_descriptor(allowed), state) is True


# ---------------------------------------------------------------------------
# Attachment payloads
# ---------------------------------------------------------------------------


def test_plan_attachments_intro_lists_path_and_workflow() -> None:
    payload = build_plan_mode_attachment(Path("C:/plans/demo.md"))
    assert "C:/plans/demo.md" in payload["content"] or "C:\\plans\\demo.md" in payload["content"]
    assert "ask_user_question" in payload["content"]
    assert "exit_plan_mode" in payload["content"]


def test_plan_attachments_reentry_includes_existing_content() -> None:
    payload = build_plan_mode_reentry_attachment(Path("C:/plans/demo.md"), "# Existing")
    assert "<plan_mode_reentry>" in payload["content"]
    assert "# Existing" in payload["content"]


def test_plan_attachments_exit_says_approved() -> None:
    payload = build_plan_mode_exit_attachment(Path("C:/plans/demo.md"), "# Done")
    assert "<plan_mode_exit>" in payload["content"]
    assert "approved" in payload["content"].lower()


def test_build_plan_attachments_consumes_flags(tmp_path: Path) -> None:
    state = RuntimeState()
    state.permission_mode = PermissionMode.PLAN
    store = PlanStore(tmp_path)
    enter_plan_mode(state, store)
    # Manually trigger exit attachment.
    state.plan.has_exited_plan_mode = True
    state.plan.needs_plan_mode_exit_attachment = True
    state.plan.needs_plan_mode_attachment = False
    attachments = build_plan_attachments_for_state(state, store)
    assert attachments
    first = attachments[0]
    assert first["type"] == "plan_mode"
    assert first["variant"] == "exit"
    # Flags should be cleared after a single consumption.
    assert state.plan.needs_plan_mode_exit_attachment is False


# ---------------------------------------------------------------------------
# Tool descriptors: structural smoke tests
# ---------------------------------------------------------------------------


def test_enter_plan_mode_tool_returns_plan_path(tmp_path: Path) -> None:
    from tools.enter_plan_mode import descriptor as enter_descriptor

    state = RuntimeState()
    state.metadata["workspace"] = str(tmp_path)
    store = PlanStore(tmp_path)
    descriptor = enter_descriptor(store)
    runtime = _runtime_for(state)
    # The handler is async; we drive it synchronously here.
    import asyncio
    result = asyncio.run(descriptor.handler({}, runtime))
    payload = json.loads(result.content)
    assert payload["permission_mode"] == "plan"
    assert payload["plan_slug"] == state.session_id
    assert Path(payload["plan_path"]).name.endswith(".md")


def test_exit_plan_mode_tool_requires_plan_mode(tmp_path: Path) -> None:
    from tools.exit_plan_mode import descriptor as exit_descriptor

    state = RuntimeState()
    state.metadata["workspace"] = str(tmp_path)
    store = PlanStore(tmp_path)
    descriptor = exit_descriptor(store)
    runtime = _runtime_for(state)
    import asyncio
    result = asyncio.run(descriptor.handler({}, runtime))
    payload = json.loads(result.content)
    assert payload["error"] == "not_in_plan_mode"


def test_exit_plan_mode_tool_reports_awaiting_approval(tmp_path: Path) -> None:
    from tools.exit_plan_mode import descriptor as exit_descriptor

    state = RuntimeState()
    state.metadata["workspace"] = str(tmp_path)
    state.permission_mode = PermissionMode.PLAN
    store = PlanStore(tmp_path)
    descriptor = exit_descriptor(store)
    runtime = _runtime_for(state)
    import asyncio
    result = asyncio.run(descriptor.handler({}, runtime))
    payload = json.loads(result.content)
    assert payload["status"] == "awaiting_approval"
    assert payload["plan_slug"]


def test_ask_user_question_tool_uses_prompter(tmp_path: Path) -> None:
    from tools.ask_user_question import descriptor as ask_descriptor
    from services.questions.types import AnswerRecord, QuestionResponse

    class _Prompter:
        async def ask_questions(self, questions):
            return QuestionResponse(
                answers=tuple(
                    AnswerRecord(question=q.question, answer=q.options[0].label)
                    for q in questions
                )
            )

    descriptor = ask_descriptor(_Prompter())
    runtime = _runtime_for(RuntimeState())
    import asyncio
    result = asyncio.run(
        descriptor.handler(
            {
                "questions": [
                    {
                        "question": "Pick one",
                        "header": "Pick",
                        "options": [
                            {"label": "A", "description": "alpha"},
                            {"label": "B", "description": "beta"},
                        ],
                        "multi_select": False,
                    }
                ]
            },
            runtime,
        )
    )
    payload = json.loads(result.content)
    assert payload["status"] == "answered"
    assert payload["answers"][0]["answer"] == "A"


def _runtime_for(state: RuntimeState) -> Any:
    """Build a minimal ``ToolRuntime`` for descriptor smoke tests."""

    from services.tools.types import ToolRuntime

    return ToolRuntime(state=state, guard=None)


# ---------------------------------------------------------------------------
# CLI /plan command
# ---------------------------------------------------------------------------


def test_cli_plan_command_enters_plan_mode(tmp_path: Path) -> None:
    from services.context.message_store import MessageStore
    from services.plans.store import PlanStore
    from ui.cli.commands import dispatch_command
    from ui.cli.types import CliRuntime

    state = RuntimeState()
    message_store = MessageStore(transcript_root=tmp_path, session_id=state.session_id, cwd=tmp_path)
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        plan_store=PlanStore(tmp_path),
    )
    result = dispatch_command(runtime, "/plan add auth flow")
    assert runtime.state.is_plan_mode()
    assert result.presentation == "inline"
    assert result.queued_prompt == "add auth flow"
    from ui.cli.views.common import render_to_text

    output = render_to_text(result.renderable)
    assert "Enabled plan mode" in output
    plan_path = tmp_path / ".onecode" / "plans" / f"{runtime.state.session_id}.md"
    assert plan_path.parent.is_dir()


def test_cli_plan_command_open_prints_path(tmp_path: Path) -> None:
    from services.context.message_store import MessageStore
    from services.plans.store import PlanStore
    from ui.cli.commands import dispatch_command
    from ui.cli.types import CliRuntime

    state = RuntimeState()
    message_store = MessageStore(transcript_root=tmp_path, session_id=state.session_id, cwd=tmp_path)
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        plan_store=PlanStore(tmp_path),
    )
    result = dispatch_command(runtime, "/plan open")
    payload = result.renderable
    from rich.console import Console
    from io import StringIO
    buffer = StringIO()
    Console(file=buffer, force_terminal=False, width=200).print(payload)
    output = buffer.getvalue()
    assert ".onecode" in output and "plans" in output


def test_cli_plan_command_show_displays_content(tmp_path: Path) -> None:
    from services.context.message_store import MessageStore
    from services.plans.store import PlanStore
    from ui.cli.commands import dispatch_command
    from ui.cli.types import CliRuntime

    state = RuntimeState()
    message_store = MessageStore(transcript_root=tmp_path, session_id=state.session_id, cwd=tmp_path)
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        plan_store=PlanStore(tmp_path),
    )
    # Pre-populate the plan file so the ``show`` subcommand has content to render.
    store = PlanStore(tmp_path)
    plan_file = store.get_or_create_plan(state)
    plan_file.write("# Pre-existing plan\n\n- step 1\n")
    result = dispatch_command(runtime, "/plan show")
    assert result.presentation == "inline"
    from ui.cli.views.common import render_to_text

    output = render_to_text(result.renderable)
    assert "# Pre-existing plan" in output
    assert "- step 1" in output


def test_cli_plan_command_approve_exits_plan_mode(tmp_path: Path) -> None:
    from services.context.message_store import MessageStore
    from services.plans.store import PlanStore
    from ui.cli.commands import dispatch_command
    from ui.cli.types import CliRuntime

    state = RuntimeState()
    message_store = MessageStore(transcript_root=tmp_path, session_id=state.session_id, cwd=tmp_path)
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        plan_store=PlanStore(tmp_path),
    )
    dispatch_command(runtime, "/plan add auth flow")
    assert runtime.state.is_plan_mode()
    result = dispatch_command(runtime, "/plan approve")
    assert not runtime.state.is_plan_mode()
    # Approve should have queued the post-exit attachment.
    assert result.attachments
    first = result.attachments[0]
    assert first["type"] == "plan_mode"
    assert first["variant"] == "exit"


def test_repl_injects_plan_attachment_on_next_turn(tmp_path: Path) -> None:
    import asyncio

    from core.stream_events import AgentEvent
    from services.context.message_store import MessageStore
    from services.plans.store import PlanStore
    from ui.cli.terminal.repl import InlineRepl
    from ui.cli.types import CliRuntime

    class _CaptureLoop:
        def __init__(self) -> None:
            self.attachments = None

        async def stream(self, prompt: str, *, attachments=None):
            assert prompt == "please plan"
            self.attachments = attachments
            yield AgentEvent(type="completed", text="done")

    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path,
        session_id=state.session_id,
        cwd=tmp_path,
    )
    plan_store = PlanStore(tmp_path)
    enter_plan_mode(state, plan_store)
    loop = _CaptureLoop()
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        loop=loop,  # type: ignore[arg-type]
        plan_store=plan_store,
    )
    repl = InlineRepl(runtime)

    async def _collect() -> None:
        events = [event async for event in repl._agent_events("please plan")]
        assert events and events[-1].type == "completed"

    asyncio.run(_collect())

    assert loop.attachments
    first = loop.attachments[0]
    assert first["type"] == "plan_mode"
    assert first["variant"] == "intro"
