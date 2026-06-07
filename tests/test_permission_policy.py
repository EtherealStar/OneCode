from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import (
    PermissionPolicy,
    PermissionRuleValue,
    PermissionUpdate,
    ProjectPermissionSettingsStore,
    SessionPermissionStore,
)
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolRuntime
from tools.bash import descriptor as bash_descriptor
from tools.edit_file import descriptor as edit_file_descriptor
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


def test_project_tool_deny_hides_tool_and_denies_execution(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ProjectPermissionSettingsStore(workspace / ".onecode" / "settings.json")
    store.apply_update(
        PermissionUpdate(
            type="addRules",
            rules=(PermissionRuleValue("edit_file"),),
            behavior="deny",
            destination="projectSettings",
        )
    )
    policy = PermissionPolicy(project_store=store)
    state = RuntimeState()
    descriptor = edit_file_descriptor()
    registry = ToolRegistry([descriptor], permission_policy=policy)

    assert registry.visible_descriptors(state) == ()
    assert policy.evaluate(
        tool_call=ToolCall(
            id="call-1",
            name="edit_file",
            input={"file_path": "a.txt", "old_string": "a", "new_string": "b"},
        ),
        descriptor=descriptor,
        classification=descriptor.classify_input(
            {"file_path": "a.txt", "old_string": "a", "new_string": "b"},
            ToolRuntime(state=state),
        ),
        guard_policies=(),
        state=state,
    ).action == "deny"


def test_project_bash_content_rules_are_deny_first(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ProjectPermissionSettingsStore(workspace / ".onecode" / "settings.json")
    store.apply_update(
        PermissionUpdate(
            type="addRules",
            rules=(
                PermissionRuleValue("bash", "npm run:*"),
                PermissionRuleValue("bash", "rm -rf:*"),
            ),
            behavior="allow",
            destination="projectSettings",
        )
    )
    store.apply_update(
        PermissionUpdate(
            type="addRules",
            rules=(PermissionRuleValue("bash", "rm -rf:*"),),
            behavior="deny",
            destination="projectSettings",
        )
    )
    policy = PermissionPolicy(project_store=store)
    state = RuntimeState()
    descriptor = bash_descriptor()
    runtime = ToolRuntime(state=state, guard=SandboxGuard(SandboxBoundary(cwd=workspace)))

    allowed_input = {"command": "npm run test"}
    allowed = policy.evaluate(
        tool_call=ToolCall(id="call-1", name="bash", input=allowed_input),
        descriptor=descriptor,
        classification=descriptor.classify_input(allowed_input, runtime),
        guard_policies=(),
        state=state,
    )
    denied_input = {"command": "rm -rf build"}
    denied = policy.evaluate(
        tool_call=ToolCall(id="call-2", name="bash", input=denied_input),
        descriptor=descriptor,
        classification=descriptor.classify_input(denied_input, runtime),
        guard_policies=(),
        state=state,
    )

    assert allowed.action == "allow"
    assert allowed.source == "project_settings"
    assert denied.action == "deny"
    assert denied.source == "project_settings"


def test_project_ask_rule_keeps_tool_visible_but_requests_permission(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = ProjectPermissionSettingsStore(workspace / ".onecode" / "settings.json")
    store.apply_update(
        PermissionUpdate(
            type="addRules",
            rules=(PermissionRuleValue("bash"),),
            behavior="ask",
            destination="projectSettings",
        )
    )
    policy = PermissionPolicy(project_store=store)
    state = RuntimeState()
    descriptor = bash_descriptor()
    registry = ToolRegistry([descriptor], permission_policy=policy)
    runtime = ToolRuntime(state=state, guard=SandboxGuard(SandboxBoundary(cwd=workspace)))
    tool_input = {"command": "git status"}

    decision = policy.evaluate(
        tool_call=ToolCall(id="call-1", name="bash", input=tool_input),
        descriptor=descriptor,
        classification=descriptor.classify_input(tool_input, runtime),
        guard_policies=(),
        state=state,
    )

    assert registry.visible_descriptors(state) == (descriptor,)
    assert decision.action == "ask"
    assert "Project permission settings" in decision.reason
