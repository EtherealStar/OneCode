from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.permissions import PermissionPolicy, PermissionResponse
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult, ToolRuntime
from tools.bash.runner import BashRunResult, GitBashRunner
from tools.bash.tool import _handle_with_runner, descriptor


class FakeRunner:
    def __init__(self, result: BashRunResult | None = None) -> None:
        self.result = result or BashRunResult(
            exit_code=0,
            stdout="ok\n",
            stderr="",
            duration_ms=5,
        )
        self.calls: list[tuple[str, Path, int]] = []

    def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult:
        self.calls.append((command, cwd, timeout_ms))
        return self.result


class MissingRunner:
    def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult:
        raise FileNotFoundError("Git Bash was not found.")


class FakePrompter:
    def __init__(self, action: str) -> None:
        self.action = action
        self.requests = []

    async def request_permission(self, request):
        self.requests.append(request)
        return PermissionResponse(action=self.action, scope="once")


def execute_results(
    executor: RegistryToolExecutor,
    tool_calls: tuple[ToolCall, ...],
    state: RuntimeState,
) -> list[ToolExecutionResult]:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(tool_calls, state):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())


def test_bash_descriptor_schema_and_prompt() -> None:
    item = descriptor()

    assert item.name == "bash"
    assert "command" in item.input_schema["properties"]
    assert item.input_schema["additionalProperties"] is False
    assert "Tree-sitter" in item.prompt


def test_git_bash_runner_decodes_bytes_without_locale(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(args, **kwargs):
        assert "text" not in kwargs
        assert kwargs["capture_output"] is True
        return SimpleNamespace(
            returncode=0,
            stdout=b"\xe4\xb8\xad\xe6\x96\x87\nbad:\xff\n",
            stderr=b"",
        )

    monkeypatch.setattr("tools.bash.runner.subprocess.run", fake_run)

    result = GitBashRunner(tmp_path / "bash.exe").run(
        "printf ok",
        cwd=tmp_path,
        timeout_ms=1000,
    )

    assert result.exit_code == 0
    assert "中文" in result.stdout
    assert "bad:" in result.stdout


def test_bash_classifies_readonly_and_write_commands() -> None:
    item = descriptor()
    runtime = ToolRuntime(RuntimeState())

    readonly = item.classify_input({"command": "git status"}, runtime)
    write = item.classify_input({"command": "echo ok > out.txt"}, runtime)

    assert readonly.read_only is True
    assert readonly.modifies_filesystem is False
    assert write.read_only is False
    assert any(target.kind == "file" and target.operation == "write" for target in write.targets)
    assert any(target.kind == "command" and target.operation == "execute" for target in write.targets)


def test_unknown_command_prompts_permission_without_file_targets(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = PermissionPolicy()
    prompter = FakePrompter("deny")
    executor = RegistryToolExecutor(
        ToolRegistry([descriptor()], permission_policy=policy),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        permission_policy=policy,
        permission_prompter=prompter,
    )

    result = execute_results(
        executor,
        (ToolCall(id="call-1", name="bash", input={"command": "npm install"}),),
        RuntimeState(),
    )[0]

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_denied"
    assert len(prompter.requests) == 1
    assert "unknown side effects" in prompter.requests[0].decision.reason


def test_non_readonly_command_requires_ask_even_without_permission_policy(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    executor = RegistryToolExecutor(
        ToolRegistry([descriptor()]),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
    )

    result = execute_results(
        executor,
        (ToolCall(id="call-1", name="bash", input={"command": "touch a.txt"}),),
        RuntimeState(),
    )[0]

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "permission_ask_required"


def test_bash_handler_uses_runner_and_interprets_no_match(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = ToolRuntime(
        RuntimeState(),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
    )
    runner = FakeRunner(BashRunResult(exit_code=1, stdout="", stderr="", duration_ms=7))

    result = _handle_with_runner({"command": "rg needle ."}, runtime, runner)

    assert result.is_error is False
    assert result.metadata["exit_code"] == 1
    assert "No matches found." in result.content
    assert runner.calls[0][1] == workspace


def test_bash_handler_returns_git_bash_not_found(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = ToolRuntime(
        RuntimeState(),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
    )

    result = _handle_with_runner({"command": "git status"}, runtime, MissingRunner())

    assert result.is_error is True
    assert json.loads(result.content)["error"] == "git_bash_not_found"
