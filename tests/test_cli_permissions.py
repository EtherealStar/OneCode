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
from ui.cli.permissions import CliPermissionPrompter, render_permission_panel


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
    assert "[p] allow this command prefix for this project" in panel


def test_bash_permission_prompter_builds_project_update(tmp_path: Path) -> None:
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

    assert response.action == "allow"
    assert response.scope == "project"
    assert len(response.permission_updates) == 1
    update = response.permission_updates[0]
    assert update.destination == "projectSettings"
    assert update.behavior == "allow"
    assert update.rules[0].tool_name == "bash"
    assert update.rules[0].rule_content == "npm run:*"
