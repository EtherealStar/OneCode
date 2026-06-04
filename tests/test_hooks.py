from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.hooks import HookEvent, HookRegistry, HookResult
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor


def make_executor(
    workspace: Path,
    hooks: HookRegistry,
    *,
    denied_patterns: tuple[str, ...] = (),
) -> tuple[RegistryToolExecutor, RuntimeState]:
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor()])
    guard = SandboxGuard(
        SandboxBoundary(cwd=workspace, denied_patterns=denied_patterns)
    )
    return RegistryToolExecutor(registry, guard=guard, hooks=hooks), RuntimeState()


def execute_one(
    executor: RegistryToolExecutor,
    state: RuntimeState,
    tool_name: str,
    tool_input: dict[str, object],
) -> ToolExecutionResult:
    return executor.execute(
        (ToolCall(id="call-1", name=tool_name, input=tool_input),),
        state,
    )[0]


def test_pre_tool_use_hook_can_block_edit_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    hooks = HookRegistry()
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda payload: HookResult(blocking_error="edits are disabled")
        if payload["descriptor"].name == "edit_file"
        else None,
    )
    executor, state = make_executor(workspace, hooks)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "a.txt", "old_string": "old", "new_string": "new"},
    )

    assert result.is_error is True
    assert "edits are disabled" in result.content
    assert target.read_text(encoding="utf-8") == "old"


def test_pre_tool_use_hook_can_update_input_before_handler(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    hooks = HookRegistry()
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda payload: HookResult(updated_input={"offset": 2, "limit": 1})
        if payload["descriptor"].name == "read_file"
        else None,
    )
    executor, state = make_executor(workspace, hooks)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt"},
    )

    assert result.is_error is False
    assert result.content == "2\ttwo"


def test_pre_tool_use_updated_input_is_rechecked_by_guard(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    hooks = HookRegistry()
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda payload: HookResult(updated_input={"file_path": str(outside)}),
    )
    executor, state = make_executor(workspace, hooks)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt"},
    )

    assert result.is_error is True
    assert "path_guard_ask_required" in result.content


def test_pre_tool_use_updated_input_is_reclassified(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one", encoding="utf-8")
    (workspace / "b.txt").write_text("two", encoding="utf-8")
    hooks = HookRegistry()
    observed_subjects: list[str] = []
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda payload: HookResult(updated_input={"file_path": "b.txt"}),
    )

    def observe(payload):
        observed_subjects.append(payload["classification"].permission_subject)
        return None

    hooks.register(HookEvent.POST_TOOL_USE, observe)
    executor, state = make_executor(workspace, hooks)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt"},
    )

    assert result.is_error is False
    assert result.content == "1\ttwo"
    assert observed_subjects == ["read_file:b.txt"]


def test_post_tool_use_hook_observes_successful_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one", encoding="utf-8")
    observed: list[ToolExecutionResult] = []
    hooks = HookRegistry()

    def observe(payload):
        observed.append(payload["result"])
        return None

    hooks.register(HookEvent.POST_TOOL_USE, observe)
    executor, state = make_executor(workspace, hooks)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt"},
    )

    assert result.is_error is False
    assert observed == [result]


def test_tool_error_hook_observes_guard_denial_and_validation_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "secret.txt").write_text("secret", encoding="utf-8")
    observed_errors: list[str] = []
    hooks = HookRegistry()

    def observe(payload):
        observed_errors.append(payload["result"].metadata["error"])
        return None

    hooks.register(HookEvent.TOOL_ERROR, observe)
    executor, state = make_executor(
        workspace,
        hooks,
        denied_patterns=("secret.txt",),
    )

    guard_result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "secret.txt"},
    )
    validation_result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt", "offset": 0},
    )

    assert guard_result.is_error is True
    assert validation_result.is_error is True
    assert observed_errors == ["path_guard_denied", "invalid_tool_input"]


def test_hook_cannot_rewrite_denied_path_to_allowed_path(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = workspace / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    allowed = workspace / "allowed.txt"
    allowed.write_text("allowed", encoding="utf-8")
    hooks = HookRegistry()
    hooks.register(
        HookEvent.PRE_TOOL_USE,
        lambda payload: HookResult(updated_input={"file_path": "allowed.txt"}),
    )
    executor, state = make_executor(
        workspace,
        hooks,
        denied_patterns=("secret.txt",),
    )

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "secret.txt"},
    )

    assert result.is_error is True
    assert "path_guard_denied" in result.content
    assert str(allowed.resolve()) not in state.metadata.get("files_read", set())
