from __future__ import annotations

from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.model.types import LLMResponse
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from ui.cli.commands import handle_command
from ui.cli.types import CliRuntime


class FakeModelClient:
    def send(self, snapshot: object) -> LLMResponse:
        raise AssertionError("model should not be called by command tests")


class FakeToolExecutor:
    def execute(self, tool_calls: tuple, state: object) -> list:
        raise AssertionError("tools should not be called by command tests")


class FakeLoop:
    def run(self, prompt: str) -> str:
        raise AssertionError("loop should not be called by command tests")


def make_runtime(tmp_path: Path) -> CliRuntime:
    state = RuntimeState(session_id="session-cli")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor()])
    executor = FakeToolExecutor()
    return CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=message_store,
        registry=registry,
        loop=FakeLoop(),  # type: ignore[arg-type]
        provider_label="TestProvider",
        model="test-model",
        model_client=FakeModelClient(),
        tool_executor=executor,  # type: ignore[arg-type]
    )


def test_help_command_prints_available_commands(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    result = handle_command(runtime, "/help")

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert "/tools" in output
    assert "/resume <target>" in output


def test_tools_command_lists_fixed_file_tools(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    handle_command(runtime, "/tools")

    output = capsys.readouterr().out
    assert "edit_file" in output
    assert "read_file" in output


def test_status_command_shows_session_and_model(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    handle_command(runtime, "/status")

    output = capsys.readouterr().out
    assert "session-cli" in output
    assert "TestProvider" in output
    assert "test-model" in output
    assert ".onecode" in output


def test_history_command_renders_recent_message_summaries(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.message_store.append_user("inspect files")
    runtime.message_store.append_assistant(
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    )
    runtime.message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call_read",
                tool_name="read_file",
                content="1\tline",
                is_error=False,
            )
        ]
    )

    handle_command(runtime, "/history 2")

    output = capsys.readouterr().out
    assert "[1] assistant: <tool call: read_file>" in output
    assert "[2] tool_result: read_file call_read: 1 line" in output
    assert "inspect files" not in output


def test_clear_command_starts_new_session_without_deleting_old(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = make_runtime(tmp_path)
    runtime.message_store.append_user("old")
    runtime.message_store.flush_transcript()
    old_session = runtime.state.session_id

    result = handle_command(runtime, "/clear")
    runtime.message_store.append_user("new")
    runtime.message_store.flush_transcript()

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert runtime.state.session_id != old_session
    assert runtime.message_store.current_messages() == (
        {"role": "user", "content": "new"},
    )
    assert (tmp_path / ".onecode" / old_session / "messages.jsonl").exists()
    assert "Started new session" in output


def test_unknown_command_does_not_exit(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    result = handle_command(runtime, "/nope")

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert "Unknown command" in output
