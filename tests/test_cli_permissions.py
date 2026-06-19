from __future__ import annotations

import asyncio
from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy
from services.tools.types import ToolCall, ToolRuntime
from tools.edit_file import descriptor as edit_file_descriptor
from tools.bash import descriptor as bash_descriptor
from tools.glob import descriptor as glob_descriptor
from tools.grep import descriptor as grep_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.write_file import descriptor as write_file_descriptor
from ui.cli.permissions import CliPermissionPrompter, render_permission_panel
from ui.cli.permissions import _confirm_options
from ui.cli.terminal.permission_prompt import _build_choices


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


def test_read_file_permission_panel_renders_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(workspace, read_file_descriptor(), {"file_path": str(outside)})

    panel = render_permission_panel(request)

    assert "Read file permission requested" in panel
    assert "normalized:" in panel
    assert "[y] allow once" in panel


def test_edit_file_permission_panel_renders_simplified_diff(tmp_path: Path) -> None:
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

    panel = render_permission_panel(request)

    assert "Edit file permission requested" in panel
    assert "Proposed edit:" in panel
    assert "- old_string: old" in panel
    assert "+ new_string: new" in panel
    assert "replace_all: True" in panel


def test_write_file_permission_panel_renders_preview_and_line_count(
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

    panel = render_permission_panel(request)

    assert "Write file permission requested" in panel
    assert "operation: write" in panel
    assert "line_count: 2" in panel
    assert "content_preview: one two" in panel


def test_search_permission_panels_have_distinct_titles(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    glob_panel = render_permission_panel(
        _request(
            workspace,
            glob_descriptor(),
            {"pattern": "*.py", "path": str(outside)},
        )
    )
    grep_panel = render_permission_panel(
        _request(
            workspace,
            grep_descriptor(),
            {"pattern": "needle", "path": str(outside)},
        )
    )

    assert "Search files permission requested" in glob_panel
    assert "pattern: *.py" in glob_panel
    assert "Search contents permission requested" in grep_panel
    assert "pattern: needle" in grep_panel


def test_cli_permission_prompter_parses_allow_session_and_deny(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(workspace, read_file_descriptor(), {"file_path": str(outside)})
    outputs: list[str] = []
    prompter = CliPermissionPrompter(
        input_func=lambda prompt: "s",
        output_func=outputs.append,
    )

    response = asyncio.run(prompter.request_permission(request))

    assert response.action == "allow"
    assert response.scope == "session"
    assert outputs and "Read file permission requested" in outputs[0]

    deny_prompter = CliPermissionPrompter(
        input_func=lambda prompt: "n",
        output_func=lambda output: None,
    )
    denied = asyncio.run(deny_prompter.request_permission(request))
    assert denied.action == "deny"

    once_prompter = CliPermissionPrompter(
        input_func=lambda prompt: "y",
        output_func=lambda output: None,
    )
    once = asyncio.run(once_prompter.request_permission(request))
    assert once.action == "allow"
    assert once.scope == "once"


def test_bash_permission_panel_renders_command_and_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        bash_descriptor(),
        {"command": "echo ok > out.txt", "description": "write output"},
    )

    panel = render_permission_panel(request)

    assert "Bash command permission requested" in panel
    assert "command: echo ok > out.txt" in panel
    assert "description: write output" in panel
    assert "read_only: False" in panel
    assert "target: out.txt" in panel
    assert "[p]" not in panel


def test_bash_permission_prompter_does_not_build_project_update(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        bash_descriptor(),
        {"command": "npm run test", "description": "test"},
    )
    prompter = CliPermissionPrompter(
        input_func=lambda prompt: "p",
        output_func=lambda output: None,
    )

    response = asyncio.run(prompter.request_permission(request))

    assert response.action == "deny"
    assert response.permission_updates == ()


def test_confirm_options_are_three_choices_for_bash(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    request = _request(
        workspace,
        bash_descriptor(),
        {"command": "npm run test", "description": "test"},
    )

    options = _confirm_options(request)

    assert [option.value for option in options] == ["y", "s", "n"]


def test_tty_permission_choices_follow_request_options(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    request = _request(workspace, read_file_descriptor(), {"file_path": str(outside)})

    choices = _build_choices(request)

    assert len(choices) == 3
    assert [choice.shortcut for choice in choices] == ["1", "2", "3"]
    assert choices[0].response.action == "allow"
    assert choices[0].response.scope == "once"
    assert choices[1].response.action == "allow"
    assert choices[1].response.scope == "session"
    assert choices[2].response.action == "deny"
    assert all(choice.response.permission_updates == () for choice in choices)
