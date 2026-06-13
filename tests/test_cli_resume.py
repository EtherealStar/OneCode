from __future__ import annotations

import asyncio
from pathlib import Path

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.tools.executor import ToolExecutionUpdate
from services.tools.file_state import FileStateCache
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from ui.cli import renderer
from ui.cli.commands import dispatch_command, resolve_resume_target
from ui.cli.resume import list_session_summaries
from ui.cli.views.common import strip_ansi
from ui.cli.types import CliRuntime


class FakeModelClient:
    async def stream(self, snapshot: object):
        raise AssertionError("model should not be called by resume tests")
        yield


class FakeToolExecutor:
    def __init__(self) -> None:
        self.file_state_cache = FileStateCache()

    def bind_file_state_cache(self, file_state_cache):
        self.file_state_cache = file_state_cache or FileStateCache()

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
    target = tmp_path / "restored.txt"
    target.write_text("content\n", encoding="utf-8")
    state = RuntimeState(session_id=session_id)
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    message_store.append_user("restore this")
    message_store.append_assistant(
        {
            "content": "",
            "tool_calls": [
                {"id": "call_read", "function": {"name": "read_file"}},
            ],
        }
    )
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call_read",
                tool_name="read_file",
                content="1\tcontent",
                metadata={"path": str(target), "offset": 1},
            )
        ]
    )
    message_store.append_assistant({"content": "restored answer"})
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
) -> None:
    messages_path = write_transcript(tmp_path, "session-old")
    runtime = make_runtime(tmp_path)
    runtime.message_store.append_user("current")

    result = dispatch_command(runtime, f"/resume {messages_path}")

    output = strip_ansi(renderer.render_to_text(result.renderable))
    assert result.runtime is not None
    assert result.runtime.state.session_id == "session-old"
    assert result.presentation == "page"
    snapshot = asyncio.run(
        result.runtime.loop.context_engine.build_for_model(result.runtime.state)
    )
    target = str(tmp_path / "restored.txt")
    assert result.runtime.message_store.current_messages() == (
        {"role": "user", "content": "restore this"},
        {
            "content": "",
            "tool_calls": [
                {"id": "call_read", "function": {"name": "read_file"}},
            ],
            "role": "assistant",
        },
        {
            "role": "tool_result",
            "tool_call_id": "call_read",
            "tool_name": "read_file",
            "content": "1\tcontent",
            "is_error": False,
            "metadata": {"path": target, "offset": 1},
        },
        {"content": "restored answer", "role": "assistant"},
    )
    assert "# Behavior Rules\n" in snapshot.system_prompt
    assert "# Tool: read_file\n" in snapshot.system_prompt
    assert target in result.runtime.state.metadata["files_read"]
    assert "Restored session session-old" in output
    assert "Session History" not in output
    assert "[read_file call_read ok]" in output
    assert "restored answer" in output
    assert "1 content" not in output


def test_resume_missing_target_keeps_current_runtime(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)

    result = dispatch_command(runtime, "/resume missing-session")

    output = strip_ansi(renderer.render_to_text(result.renderable))
    assert result.runtime is None
    assert runtime.state.session_id == "session-current"
    assert "No session title matches" in output


def test_resume_without_target_requests_selector(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    result = dispatch_command(runtime, "/resume")

    assert result.interaction == "resume_selector"
    assert result.runtime is None


def test_session_summaries_derive_titles_and_skip_bad_records(tmp_path: Path) -> None:
    messages_path = write_transcript(tmp_path, "session-old")
    with messages_path.open("a", encoding="utf-8") as handle:
        handle.write("{bad json\n")

    summaries = list_session_summaries(tmp_path)

    assert len(summaries) == 1
    assert summaries[0].session_id == "session-old"
    assert summaries[0].title == "restore this"
    assert summaries[0].message_count == 4
    assert summaries[0].updated_at is not None


def test_resume_command_searches_session_titles(tmp_path: Path) -> None:
    write_transcript(tmp_path, "session-old")
    runtime = make_runtime(tmp_path)

    result = dispatch_command(runtime, "/resume restore")

    assert result.runtime is not None
    assert result.runtime.state.session_id == "session-old"


def test_resume_command_lists_multiple_title_matches(tmp_path: Path) -> None:
    write_transcript(tmp_path, "session-one")
    write_transcript(tmp_path, "session-two")
    runtime = make_runtime(tmp_path)

    result = dispatch_command(runtime, "/resume restore")
    output = strip_ansi(renderer.render_to_text(result.renderable))

    assert result.runtime is None
    assert result.presentation == "page"
    assert "session-one" in output
    assert "session-two" in output


def test_continue_alias_matches_resume(tmp_path: Path) -> None:
    write_transcript(tmp_path, "session-old")
    runtime = make_runtime(tmp_path)

    result = dispatch_command(runtime, "/continue session-old")

    assert result.runtime is not None
    assert result.runtime.state.session_id == "session-old"
