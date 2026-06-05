from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.tools.executor import ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from ui.cli.commands import handle_command, resolve_resume_target
from ui.cli.types import CliRuntime


class FakeModelClient:
    async def stream(self, snapshot: object):
        raise AssertionError("model should not be called by resume tests")
        yield


class FakeToolExecutor:
    async def execute(self, tool_calls: tuple, state: object):
        if False:
            yield ToolExecutionUpdate(type="result")


class FakeLoop:
    async def stream(self, prompt: str):
        raise AssertionError("loop should not be called by resume tests")
        yield


def make_runtime(tmp_path: Path, session_id: str = "session-current") -> CliRuntime:
    state = RuntimeState(session_id=session_id)
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


def write_transcript(tmp_path: Path, session_id: str) -> Path:
    state = RuntimeState(session_id=session_id)
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    message_store.append_user("restore this")
    message_store.append_assistant({"content": "restored answer"})
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call_read",
                tool_name="read_file",
                content="1\tcontent",
            )
        ]
    )
    message_store.flush_transcript()
    return tmp_path / ".onecode" / session_id / "messages.jsonl"


def test_resolve_resume_target_accepts_session_id(tmp_path: Path) -> None:
    messages_path = write_transcript(tmp_path, "session-old")

    transcript_store = resolve_resume_target(tmp_path, "session-old")

    assert transcript_store.session_id == "session-old"
    assert transcript_store.messages_path == messages_path


def test_resolve_resume_target_accepts_messages_jsonl_path(tmp_path: Path) -> None:
    messages_path = write_transcript(tmp_path, "session-old")

    transcript_store = resolve_resume_target(tmp_path, str(messages_path))

    assert transcript_store.session_id == "session-old"
    assert transcript_store.messages_path == messages_path


def test_resume_command_replaces_runtime_and_restores_messages(
    tmp_path: Path,
    capsys: Any,
) -> None:
    messages_path = write_transcript(tmp_path, "session-old")
    runtime = make_runtime(tmp_path)
    runtime.message_store.append_user("current")

    result = handle_command(runtime, f"/resume {messages_path}")

    output = capsys.readouterr().out
    assert result.runtime is not None
    assert result.runtime.state.session_id == "session-old"
    snapshot = asyncio.run(
        result.runtime.loop.context_engine.build_for_model(result.runtime.state)
    )
    assert result.runtime.message_store.current_messages() == (
        {"role": "user", "content": "restore this"},
        {"content": "restored answer", "role": "assistant"},
        {
            "role": "tool_result",
            "tool_call_id": "call_read",
            "tool_name": "read_file",
            "content": "1\tcontent",
            "is_error": False,
            "metadata": {},
        },
    )
    assert "# Behavior Rules\n" in snapshot.system_prompt
    assert "# Tool: read_file\n" in snapshot.system_prompt
    assert "Restored session session-old" in output


def test_resume_missing_target_keeps_current_runtime(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = make_runtime(tmp_path)

    result = handle_command(runtime, "/resume missing-session")

    output = capsys.readouterr().out
    assert result.runtime is None
    assert runtime.state.session_id == "session-current"
    assert "Transcript does not exist" in output
