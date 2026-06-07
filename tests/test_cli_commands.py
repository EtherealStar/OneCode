from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.compaction import SessionMemoryStore
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.compaction.types import CompactionResult, CompactionTrigger
from services.tools.executor import ToolExecutionUpdate
from services.observability import JsonlTraceSink, TraceRecorder
from services.context.current_model_context import CurrentModelContext
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from services.tasks import TaskStore
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.write_file import descriptor as write_file_descriptor
from ui.cli.commands import handle_command
from ui.cli.types import CliRuntime
from services.mcp.types import McpConnectionSnapshot, McpDiscoveredTool, McpServerStatus


class FakeModelClient:
    async def stream(self, snapshot: object):
        raise AssertionError("model should not be called by command tests")
        yield


class FakeToolExecutor:
    async def execute(self, tool_calls: tuple, state: object):
        raise AssertionError("tools should not be called by command tests")
        yield ToolExecutionUpdate(type="result")


class BindableFakeToolExecutor(FakeToolExecutor):
    def __init__(self) -> None:
        self.result_store = None

    def bind_result_store(self, result_store: object) -> None:
        self.result_store = result_store


class FakeLoop:
    async def stream(self, prompt: str):
        raise AssertionError("loop should not be called by command tests")
        yield


class FakeCompactionService:
    def __init__(self) -> None:
        self.config = type(
            "Config",
            (),
            {"auto_compact_threshold_tokens": 93000},
        )()
        self.focus: str | None = None

    async def manual_compact(self, state: RuntimeState, *, focus: str | None = None):
        self.focus = focus
        state.metadata["last_compaction"] = {
            "trigger": "manual",
            "token_before": 100,
            "token_after": 25,
        }
        return CompactionResult(
            trigger=CompactionTrigger.MANUAL,
            messages=({"role": "user", "content": "summary"},),
            token_before=100,
            token_after=25,
        )


class BindableFakeCompactionService(FakeCompactionService):
    def __init__(self) -> None:
        super().__init__()
        self.bound_message_store = None
        self.bound_session_memory_store = None
        self.bound_result_store = None

    def bind_runtime(
        self,
        *,
        message_store: object | None = None,
        session_memory_store: object | None = None,
        result_store: object | None = None,
        subagent_runner: object | None = None,
    ) -> None:
        self.bound_message_store = message_store
        self.bound_session_memory_store = session_memory_store
        self.bound_result_store = result_store


class FakeSubagentRunner:
    def __init__(self) -> None:
        self.parent_message_store = None

    def bind_parent_message_store(self, message_store: object) -> None:
        self.parent_message_store = message_store


class FakeMcpManager:
    def snapshot(self) -> McpConnectionSnapshot:
        return McpConnectionSnapshot(
            statuses=(
                McpServerStatus(
                    name="docs",
                    transport="stdio",
                    state="connected",
                    tool_count=1,
                    instructions_present=True,
                ),
            ),
            tools=(
                McpDiscoveredTool(
                    server_name="docs",
                    normalized_server_name="docs",
                    tool_name="search.docs",
                    normalized_tool_name="search_docs",
                    descriptor_name="mcp__docs__search_docs",
                    description="Search docs.",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
            instructions={"docs": "Use docs."},
        )

    async def close_all(self) -> None:
        return None


def make_runtime(tmp_path: Path) -> CliRuntime:
    state = RuntimeState(session_id="session-cli")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    registry = ToolRegistry(
        [read_file_descriptor(), edit_file_descriptor(), write_file_descriptor()]
    )
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
    assert "/tasks" in output
    assert "/mcp [tools]" in output
    assert "/resume <target>" in output
    assert "/permissions" not in output


def test_tools_command_lists_fixed_file_tools(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    handle_command(runtime, "/tools")

    output = capsys.readouterr().out
    assert "edit_file" in output
    assert "read_file" in output
    assert "write_file" in output


def test_status_command_shows_session_and_model(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    handle_command(runtime, "/status")

    output = capsys.readouterr().out
    assert "session-cli" in output
    assert "TestProvider" in output
    assert "test-model" in output
    assert ".onecode" in output


def test_mcp_command_renders_server_status_and_tools(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = replace(make_runtime(tmp_path), mcp_manager=FakeMcpManager())  # type: ignore[arg-type]

    handle_command(runtime, "/mcp tools")

    output = capsys.readouterr().out
    assert "MCP servers:" in output
    assert "docs [stdio] connected tools=1 instructions=yes" in output
    assert "mcp__docs__search_docs: docs/search.docs" in output


def test_compact_command_triggers_manual_compact(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)
    compaction = FakeCompactionService()
    runtime = replace(runtime, compaction_service=compaction)  # type: ignore[arg-type]

    result = handle_command(runtime, "/compact current goal")

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert compaction.focus == "current goal"
    assert "Compacted session:" in output
    assert "100 -> 25" in output


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


def test_trace_command_renders_recent_trace_records(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = make_runtime(tmp_path)
    sink = JsonlTraceSink(
        tmp_path / ".onecode",
        runtime.state.session_id,
        flush_interval_seconds=60,
    )
    recorder = TraceRecorder(
        session_id=runtime.state.session_id,
        workspace=tmp_path,
        sink=sink,
    )
    recorder.event("transition", {"transition": "completed"})
    recorder.flush()
    runtime = replace(runtime, trace_recorder=recorder)

    handle_command(runtime, "/trace 1")

    output = capsys.readouterr().out
    assert "Recent trace:" in output
    assert "transition" in output
    assert "transition=completed" in output


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


def test_clear_command_rebinds_session_scoped_services(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runtime = make_runtime(tmp_path)
    old_session = runtime.state.session_id
    old_memory_store = SessionMemoryStore(
        runtime.message_store.transcript_store.session_dir
    )
    executor = BindableFakeToolExecutor()
    compaction = BindableFakeCompactionService()
    current_context = CurrentModelContext(
        ContextSnapshot(system_prompt="old", messages=())
    )
    subagent_runner = FakeSubagentRunner()
    runtime = replace(
        runtime,
        tool_executor=executor,  # type: ignore[arg-type]
        compaction_service=compaction,  # type: ignore[arg-type]
        current_model_context=current_context,
        subagent_runner=subagent_runner,  # type: ignore[arg-type]
        session_memory_store=old_memory_store,
    )

    result = handle_command(runtime, "/clear")
    cleared = result.runtime

    capsys.readouterr()
    assert cleared is not None
    assert cleared.state.session_id != old_session
    assert current_context.snapshot is None
    assert executor.result_store is not None
    assert str(cleared.state.session_id) in str(executor.result_store.results_dir)
    assert compaction.bound_message_store is cleared.message_store
    assert compaction.bound_session_memory_store is not old_memory_store
    assert compaction.bound_result_store is executor.result_store
    assert subagent_runner.parent_message_store is cleared.message_store
    assert cleared.loop.message_store is cleared.message_store


def test_unknown_command_does_not_exit(tmp_path: Path, capsys: Any) -> None:
    runtime = make_runtime(tmp_path)

    result = handle_command(runtime, "/nope")

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert "Unknown command" in output


def test_tasks_command_renders_empty_task_list(tmp_path: Path, capsys: Any) -> None:
    runtime = replace(make_runtime(tmp_path), task_store=TaskStore(tmp_path))

    result = handle_command(runtime, "/tasks")

    output = capsys.readouterr().out
    assert result.should_exit is False
    assert "No tasks found for task list session-cli." in output
    assert runtime.state.metadata["task_list_id"] == "session-cli"


def test_tasks_command_renders_existing_tasks(tmp_path: Path, capsys: Any) -> None:
    task_store = TaskStore(tmp_path)
    first = task_store.create_task("session-cli", subject="Schema", description="A")
    second = task_store.create_task("session-cli", subject="API", description="B")
    task_store.block_task("session-cli", first.id, second.id)
    runtime = replace(make_runtime(tmp_path), task_store=task_store)

    handle_command(runtime, "/tasks")

    output = capsys.readouterr().out
    assert "Tasks:" in output
    assert "task list: session-cli" in output
    assert ".onecode" in output
    assert "#1 [pending] Schema" in output
    assert "#2 [pending] API [blocked by #1]" in output


def test_tasks_command_reports_store_errors(tmp_path: Path, capsys: Any) -> None:
    task_store = TaskStore(tmp_path)
    task_dir = task_store.tasks_dir("session-cli")
    task_dir.mkdir(parents=True)
    (task_dir / "1.json").write_text("{bad json", encoding="utf-8")
    runtime = replace(make_runtime(tmp_path), task_store=task_store)

    handle_command(runtime, "/tasks")

    output = capsys.readouterr().out
    assert "Error: Could not read task file" in output
