from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult, ToolRuntime
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.write_file import descriptor as write_file_descriptor


def make_executor(
    workspace: Path,
    *,
    denied_patterns: tuple[str, ...] = (),
) -> tuple[RegistryToolExecutor, RuntimeState]:
    guard = SandboxGuard(
        SandboxBoundary(cwd=workspace, denied_patterns=denied_patterns)
    )
    registry = ToolRegistry(
        [read_file_descriptor(), edit_file_descriptor(), write_file_descriptor()]
    )
    return RegistryToolExecutor(registry, guard=guard), RuntimeState()


def execute_one(
    executor: RegistryToolExecutor,
    state: RuntimeState,
    tool_name: str,
    tool_input: dict[str, object],
    *,
    call_id: str = "call-1",
) -> ToolExecutionResult:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(
            (ToolCall(id=call_id, name=tool_name, input=tool_input),),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())[0]


def execute_results(
    executor: RegistryToolExecutor,
    state: RuntimeState,
    tool_calls: tuple[ToolCall, ...],
) -> list[ToolExecutionResult]:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(tool_calls, state):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())


def test_file_tool_descriptors_classify_input() -> None:
    cases = [
        (
            read_file_descriptor,
            {"file_path": "a.txt"},
            True,
            False,
            True,
            "read",
            math.inf,
            "read_file:a.txt",
        ),
        (
            edit_file_descriptor,
            {"file_path": "a.txt", "old_string": "old", "new_string": "new"},
            False,
            True,
            False,
            "write",
            50_000,
            "edit_file:a.txt",
        ),
        (
            write_file_descriptor,
            {"file_path": "a.txt", "content": "new"},
            False,
            True,
            False,
            "write",
            50_000,
            "write_file:a.txt",
        ),
    ]

    for (
        descriptor_factory,
        tool_input,
        read_only,
        modifies_filesystem,
        concurrency_safe,
        operation,
        max_result_size_chars,
        permission_subject,
    ) in cases:
        classification = descriptor_factory().classify_input(
            tool_input,
            ToolRuntime(state=RuntimeState()),
        )

        assert classification.read_only is read_only
        assert classification.modifies_filesystem is modifies_filesystem
        assert classification.concurrency_safe is concurrency_safe
        assert classification.targets[0].kind == "file"
        assert classification.targets[0].operation == operation
        assert classification.targets[0].value == "a.txt"
        assert classification.result_policy.max_result_size_chars == max_result_size_chars
        assert classification.permission_subject == permission_subject


def test_read_file_returns_line_numbered_workspace_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt", "offset": 2, "limit": 2},
    )

    assert result.is_error is False
    assert result.tool_call_id == "call-1"
    assert result.tool_name == "read_file"
    assert result.content == "2\ttwo\n3\tthree"
    assert str(target.resolve()) in state.metadata["files_read"]
    cached = executor.file_state_cache.get(target)
    assert cached is not None
    assert cached.path == target.resolve()
    assert cached.content == "one\ntwo\nthree\n"
    assert cached.partial is True
    assert cached.mtime_ns == target.stat().st_mtime_ns


def test_read_file_replaces_invalid_utf8_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_bytes(b"ok\nbad:\xff\nemoji:\xf0\x9f\x98\x80\n")
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "a.txt"},
    )

    assert result.is_error is False
    assert "1\tok" in result.content
    assert "2\tbad:" in result.content
    assert "3\temoji:" in result.content


def test_read_file_handler_does_not_record_files_read_directly(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("one", encoding="utf-8")
    state = RuntimeState()
    runtime = ToolRuntime(
        state=state,
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
    )

    result = read_file_descriptor().handler({"file_path": "a.txt"}, runtime)

    assert result.is_error is False
    assert result.metadata["path"] == str(target.resolve())
    assert state.metadata.get("files_read") is None


def test_read_file_rejects_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "subdir").mkdir()
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "subdir"},
    )

    assert result.is_error is True
    assert result.metadata["error"] == "path_is_directory"


def test_read_file_denied_path_returns_error_without_reading(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "secret.txt"
    target.write_text("classified", encoding="utf-8")
    executor, state = make_executor(workspace, denied_patterns=("secret.txt",))

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": "secret.txt"},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "path_guard_denied"
    assert str(target.resolve()) not in state.metadata.get("files_read", set())


def test_read_file_external_path_returns_ask_required_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "read_file",
        {"file_path": str(outside)},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "permission_ask_required"
    assert payload["decision"] == "ask"
    assert payload["guard_policies"][0]["decision"] == "external_directory"


def test_edit_file_requires_prior_read_for_existing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "a.txt", "old_string": "old", "new_string": "new"},
    )

    assert result.is_error is True
    assert result.metadata["error"] == "file_not_read"
    assert target.read_text(encoding="utf-8") == "old"


def test_edit_file_replaces_single_exact_match_after_read(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("alpha beta gamma", encoding="utf-8")
    executor, state = make_executor(workspace)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "a.txt", "old_string": "beta", "new_string": "BETA"},
    )

    assert result.is_error is False
    assert result.metadata["replacement_count"] == 1
    assert target.read_text(encoding="utf-8") == "alpha BETA gamma"
    cached = executor.file_state_cache.get(target)
    assert cached is not None
    assert cached.content == "alpha BETA gamma"
    assert cached.partial is False


def test_read_then_edit_same_response_uses_executor_recorded_files_read(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)

    results = execute_results(
        executor,
        state,
        (
            ToolCall(id="call-read", name="read_file", input={"file_path": "a.txt"}),
            ToolCall(
                id="call-edit",
                name="edit_file",
                input={
                    "file_path": "a.txt",
                    "old_string": "old",
                    "new_string": "new",
                },
            ),
        ),
    )

    assert [result.is_error for result in results] == [False, False]
    assert target.read_text(encoding="utf-8") == "new"
    assert str(target.resolve()) in state.metadata["files_read"]


def test_edit_file_rejects_multiple_matches_without_replace_all(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("x x x", encoding="utf-8")
    executor, state = make_executor(workspace)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "a.txt", "old_string": "x", "new_string": "y"},
    )

    assert result.is_error is True
    assert result.metadata["error"] == "multiple_matches"
    assert target.read_text(encoding="utf-8") == "x x x"


def test_edit_file_replace_all_replaces_every_exact_match(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("x x x", encoding="utf-8")
    executor, state = make_executor(workspace)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})

    result = execute_one(
        executor,
        state,
        "edit_file",
        {
            "file_path": "a.txt",
            "old_string": "x",
            "new_string": "y",
            "replace_all": True,
        },
    )

    assert result.is_error is False
    assert result.metadata["replacement_count"] == 3
    assert target.read_text(encoding="utf-8") == "y y y"


def test_edit_file_creates_new_file_when_old_string_is_empty(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "nested" / "new.txt"
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "nested/new.txt", "old_string": "", "new_string": "created"},
    )

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == "created"
    assert str(target.resolve()) in state.metadata["files_read"]
    cached = executor.file_state_cache.get(target)
    assert cached is not None
    assert cached.content == "created"


def test_edit_file_denied_write_does_not_modify_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "secret.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace, denied_patterns=("secret.txt",))
    state.metadata["files_read"] = {str(target.resolve())}

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": "secret.txt", "old_string": "old", "new_string": "new"},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "path_guard_denied"
    assert target.read_text(encoding="utf-8") == "old"


def test_edit_file_external_write_returns_ask_without_writing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)
    state.metadata["files_read"] = {str(outside.resolve())}

    result = execute_one(
        executor,
        state,
        "edit_file",
        {"file_path": str(outside), "old_string": "old", "new_string": "new"},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "permission_ask_required"
    assert outside.read_text(encoding="utf-8") == "old"


def test_write_file_creates_new_file_and_parent_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "nested" / "new.txt"
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "nested/new.txt", "content": "one\ntwo\n"},
    )

    assert result.is_error is False
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"
    assert result.metadata["operation"] == "create"
    assert result.metadata["line_count"] == 2
    assert str(target.resolve()) in state.metadata["files_read"]
    cached = executor.file_state_cache.get(target)
    assert cached is not None
    assert cached.content == "one\ntwo\n"
    assert cached.partial is False


def test_write_file_requires_prior_full_read_for_existing_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)

    unread = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "a.txt", "content": "new"},
    )
    execute_one(executor, state, "read_file", {"file_path": "a.txt", "limit": 1})
    partial = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "a.txt", "content": "new"},
    )

    assert unread.is_error is True
    assert unread.metadata["error"] == "file_not_read"
    assert partial.is_error is True
    assert partial.metadata["error"] == "file_not_fully_read"
    assert target.read_text(encoding="utf-8") == "old"


def test_write_file_overwrites_after_full_read_and_returns_diff(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    executor, state = make_executor(workspace)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "a.txt", "content": "alpha\nBETA\n"},
    )

    assert result.is_error is False
    assert result.metadata["operation"] == "update"
    assert result.metadata["line_count"] == 2
    assert result.metadata["diff_truncated"] is False
    assert "-beta" in result.content
    assert "+BETA" in result.content
    assert target.read_text(encoding="utf-8") == "alpha\nBETA\n"
    cached = executor.file_state_cache.get(target)
    assert cached is not None
    assert cached.content == "alpha\nBETA\n"


def test_write_file_rejects_file_modified_after_read(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)
    execute_one(executor, state, "read_file", {"file_path": "a.txt"})
    target.write_text("external", encoding="utf-8")

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "a.txt", "content": "new"},
    )

    assert result.is_error is True
    assert result.metadata["error"] == "file_unexpectedly_modified"
    assert target.read_text(encoding="utf-8") == "external"


def test_write_file_rejects_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "subdir").mkdir()
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "subdir", "content": "new"},
    )

    assert result.is_error is True
    assert result.metadata["error"] == "path_is_directory"


def test_write_file_denied_path_does_not_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "secret.txt"
    target.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace, denied_patterns=("secret.txt",))
    execute_one(executor, state, "read_file", {"file_path": "secret.txt"})

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": "secret.txt", "content": "new"},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "path_guard_denied"
    assert target.read_text(encoding="utf-8") == "old"


def test_write_file_external_path_returns_ask_without_writing(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("old", encoding="utf-8")
    executor, state = make_executor(workspace)

    result = execute_one(
        executor,
        state,
        "write_file",
        {"file_path": str(outside), "content": "new"},
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["error"] == "permission_ask_required"
    assert outside.read_text(encoding="utf-8") == "old"
