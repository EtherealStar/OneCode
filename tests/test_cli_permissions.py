from __future__ import annotations

import asyncio
from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy
from services.tools.types import ToolCall, ToolRuntime
from tools.bash import descriptor as bash_descriptor
from tools.edit_file import descriptor as edit_file_descriptor
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.write_file import descriptor as write_file_descriptor
from ui.cli.permissions import render_permission_request_summary
from ui.cli.terminal.interaction_host import TerminalInteractionHost
from ui.cli.terminal.permission_modal import (
    PermissionModal,
    build_permission_choices,
    render_permission_modal_ansi,
)
from ui.cli.terminal.permission_prompt import TtyPermissionPrompter


def _request(workspace: Path, descriptor, tool_input: dict[str, object]):
    state = RuntimeState()
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    runtime = ToolRuntime(state=state, guard=guard)
    classification = descriptor.classify_input(tool_input, runtime)
    guard_policies = tuple(
        guard.check_path(target.value, operation=target.operation, kind=target.kind)
        for target in classification.targets
        if target.kind in {"file", "directory"}
    )
    decision = PermissionPolicy().evaluate(
        tool_call=ToolCall(id="call-1", name=descriptor.name, input=dict(tool_input)),
        descriptor=descriptor,
        classification=classification,
        guard_policies=guard_policies,
        state=state,
    )
    return PermissionPolicy().request_for_decision(
        tool_call=ToolCall(id="call-1", name=descriptor.name, input=dict(tool_input)),
        descriptor=descriptor,
        classification=classification,
        decision=decision,
        tool_input=dict(tool_input),
    )


def test_read_file_permission_summary_renders_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(workspace, read_file_descriptor(), {"file_path": str(outside)})

    panel = render_permission_request_summary(request)

    assert "Read_file" in panel
    assert "normalized:" in panel
    assert "Do you want to proceed?" not in panel


def test_edit_file_permission_summary_renders_simplified_diff(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(
        workspace,
        edit_file_descriptor(),
        {
            "file_path": str(outside),
            "old_string": "old",
            "new_string": "new",
            "replace_all": True,
        },
    )

    panel = render_permission_request_summary(request)

    assert "Edit_file" in panel
    assert "Proposed edit:" in panel
    assert "- old_string: old" in panel
    assert "+ new_string: new" in panel
    assert "replace_all: True" in panel


def test_write_file_permission_summary_renders_preview_and_line_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(
        workspace,
        write_file_descriptor(),
        {"file_path": str(outside), "content": "one\ntwo\n"},
    )

    panel = render_permission_request_summary(request)

    assert "Write_file" in panel
    assert "operation: write" in panel
    assert "line_count: 2" in panel
    assert "content_preview: one two" in panel


def test_search_permission_summaries_have_distinct_titles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    glob_panel = render_permission_request_summary(
        _request(
            workspace,
            glob_descriptor(),
            {"pattern": "*.py", "path": str(outside)},
        )
    )
    grep_panel = render_permission_request_summary(
        _request(
            workspace,
            grep_descriptor(),
            {"pattern": "needle", "path": str(outside)},
        )
    )

    assert "Glob" in glob_panel
    assert "pattern: *.py" in glob_panel
    assert "Grep" in grep_panel
    assert "pattern: needle" in grep_panel


def test_bash_permission_summary_renders_command_and_no_project_choice(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        bash_descriptor(),
        {"command": "echo ok > out.txt", "description": "write output"},
    )

    panel = render_permission_request_summary(request)

    assert "Bash" in panel
    assert "command: echo ok > out.txt" in panel
    assert "description: write output" in panel
    assert "read_only: False" in panel
    assert "target: out.txt" in panel
    assert "[p]" not in panel
    assert "project" not in panel.lower()


def test_permission_modal_choices_follow_request_options(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(workspace, read_file_descriptor(), {"file_path": str(outside)})

    choices = build_permission_choices(request)

    assert len(choices) == 3
    assert [choice.shortcut for choice in choices] == ["1", "2", "3"]
    assert choices[0].label == "Yes"
    assert choices[0].response.action == "allow"
    assert choices[0].response.scope == "once"
    assert choices[1].label.startswith("Yes, allow")
    assert choices[1].response.action == "allow"
    assert choices[1].response.scope == "session"
    assert choices[2].label == "No"
    assert choices[2].response.action == "deny"
    assert all(choice.response.permission_updates == () for choice in choices)


def test_permission_modal_renders_request_and_three_choices(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        bash_descriptor(),
        {"command": "npm run test", "description": "test"},
    )
    loop = asyncio.new_event_loop()
    modal = PermissionModal(
        request=request,
        choices=build_permission_choices(request),
        future=loop.create_future(),
    )

    try:
        rendered = str(render_permission_modal_ansi(modal, width=100))
    finally:
        loop.close()

    assert "Bash" in rendered
    assert "command: npm run test" in rendered
    assert "Do you want to proceed?" in rendered
    assert "1. Yes" in rendered
    assert "2. Yes, allow" in rendered
    assert "3. No" in rendered
    assert "project" not in rendered.lower()


def test_interaction_host_returns_session_choice_without_project_update(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace, read_file_descriptor(), {"file_path": str(tmp_path)})
    host = TerminalInteractionHost()
    host.bind_app(_FakeRunningApp())

    async def run_prompt():
        task = asyncio.create_task(host.request_permission(request))
        await asyncio.sleep(0)
        assert host.active_permission is not None
        assert host.handle_key("2") is True
        return await task

    response = asyncio.run(run_prompt())

    assert response.action == "allow"
    assert response.scope == "session"
    assert response.permission_updates == ()
    assert host.active_permission is None


def test_tty_permission_prompter_delegates_to_host(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(workspace, read_file_descriptor(), {"file_path": str(tmp_path)})
    host = TerminalInteractionHost()
    host.bind_app(_FakeRunningApp())
    prompter = TtyPermissionPrompter(host)

    async def run_prompt():
        task = asyncio.create_task(prompter.request_permission(request))
        await asyncio.sleep(0)
        assert host.active_permission is not None
        host.handle_key("1")
        return await task

    response = asyncio.run(run_prompt())

    assert response.action == "allow"
    assert response.scope == "once"


class _FakeRunningApp:
    is_running = True

    def invalidate(self) -> None:
        pass
