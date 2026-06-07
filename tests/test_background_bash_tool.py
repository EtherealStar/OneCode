from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.types import ToolRuntime
from tools.bash.tool import _handle_with_runner
from tools.bash.runner import BashRunResult


class FakeRunner:
    def run(self, command: str, *, cwd: Path, timeout_ms: int) -> BashRunResult:
        raise AssertionError("foreground runner should not be used")


class FakeManager:
    def __init__(self) -> None:
        self.calls = []

    def start_bash(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Task",
            (),
            {
                "id": "b_fake",
                "type": "local_bash",
                "status": "running",
                "output_file": ".onecode/session/background-tasks/b_fake.output",
            },
        )()


def test_background_bash_starts_manager_and_returns_task(monkeypatch, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = ToolRuntime(
        RuntimeState(session_id="session"),
        guard=SandboxGuard(SandboxBoundary(cwd=workspace)),
        tool_call_id="call-bash",
    )
    manager = FakeManager()
    monkeypatch.setattr("tools.bash.tool.find_git_bash", lambda: tmp_path / "bash.exe")

    result = _handle_with_runner(
        {
            "command": "npm test",
            "run_in_background": True,
            "description": "tests",
        },
        runtime,
        FakeRunner(),
        background_task_manager=manager,
    )

    assert result.is_error is False
    assert "task_id: b_fake" in result.content
    assert result.metadata["background"] is True
    assert manager.calls[0]["timeout_ms"] is None
    assert manager.calls[0]["tool_use_id"] == "call-bash"


def test_background_bash_requires_manager(tmp_path: Path) -> None:
    runtime = ToolRuntime(
        RuntimeState(),
        guard=SandboxGuard(SandboxBoundary(cwd=tmp_path)),
    )

    result = _handle_with_runner(
        {"command": "git status", "run_in_background": True},
        runtime,
        FakeRunner(),
    )

    assert result.is_error is True
    assert result.metadata["error"] == "background_tasks_not_enabled"
