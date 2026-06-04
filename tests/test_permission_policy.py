from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy, SessionPermissionStore
from services.tools.types import ToolCall, ToolRuntime
from tools.read_file import descriptor as read_file_descriptor


def _decision(
    workspace: Path,
    target: str,
    *,
    denied_patterns: tuple[str, ...] = (),
    store: SessionPermissionStore | None = None,
):
    descriptor = read_file_descriptor()
    state = RuntimeState()
    guard = SandboxGuard(
        SandboxBoundary(cwd=workspace, denied_patterns=denied_patterns)
    )
    runtime = ToolRuntime(state=state, guard=guard)
    tool_input = {"file_path": target}
    classification = descriptor.classify_input(tool_input, runtime)
    guard_policy = guard.check_path(target, operation="read", kind="file")
    return PermissionPolicy(store).evaluate(
        tool_call=ToolCall(id="call-1", name="read_file", input=tool_input),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(guard_policy,),
        state=state,
    )


def test_permission_policy_denies_before_session_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = workspace / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    store = SessionPermissionStore()
    store.allow_directory(
        tool_name="read_file",
        operation="read",
        directory=workspace,
    )

    decision = _decision(
        workspace,
        "secret.txt",
        denied_patterns=("secret.txt",),
        store=store,
    )

    assert decision.action == "deny"
    assert decision.source == "guard"


def test_permission_policy_asks_for_protected_project_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    target = workspace / ".git" / "config"
    target.write_text("config", encoding="utf-8")

    decision = _decision(workspace, ".git/config")

    assert decision.action == "ask"
    assert ".git" in decision.reason


def test_permission_policy_asks_for_external_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    decision = _decision(workspace, str(outside))

    assert decision.action == "ask"
    assert "outside the configured sandbox boundary" in decision.reason


def test_session_allow_covers_ask_for_same_tool_operation_and_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "a.txt"
    target.write_text("outside", encoding="utf-8")
    store = SessionPermissionStore()
    store.allow_directory(
        tool_name="read_file",
        operation="read",
        directory=outside,
    )

    decision = _decision(workspace, str(target), store=store)

    assert decision.action == "allow"
    assert decision.source == "session"


def test_tool_level_session_deny_hides_tool_at_policy_level(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("a", encoding="utf-8")
    store = SessionPermissionStore()
    store.deny_tool("read_file")

    decision = _decision(workspace, "a.txt", store=store)

    assert decision.action == "deny"
    assert decision.source == "tool_policy"
