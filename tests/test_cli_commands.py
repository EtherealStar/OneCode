from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from infrastructure.filesystem.onecode_paths import session_messages_path, session_dir, sessions_dir
from services.background_tasks import BackgroundTaskManager
from services.compaction import SessionMemoryStore
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.compaction.types import CompactionResult, CompactionTrigger
from services.mcp.types import McpConnectionSnapshot, McpDiscoveredTool, McpServerStatus
from services.permissions import PermissionPolicy, ProjectPermissionSettingsStore, SessionPermissionStore
from services.tasks import TaskStore
from services.tools.executor import ToolExecutionUpdate
from services.tools.registry import ToolRegistry
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor
from tools.write_file import descriptor as write_file_descriptor
from ui.cli import renderer
from ui.cli.commands import dispatch_command, visible_commands
from ui.cli.types import CliRuntime
from ui.cli.views.common import strip_ansi


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
        _ = subagent_runner
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
                    annotations={"readOnlyHint": True},
                ),
            ),
            instructions={"docs": "Use docs."},
        )

    async def close_all(self) -> None:
        return None


class FakeUntrustedMcpManager:
    def snapshot(self) -> McpConnectionSnapshot:
        return McpConnectionSnapshot(
            statuses=(
                McpServerStatus(
                    name="docs",
                    transport="stdio",
                    state="untrusted",
                ),
            ),
        )


def make_runtime(tmp_path: Path) -> CliRuntime:
    state = RuntimeState(session_id="session-cli")
    message_store = MessageStore(
        transcript_root=sessions_dir(tmp_path),
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


def run_command(runtime: CliRuntime, line: str) -> tuple[Any, str]:
    result = dispatch_command(runtime, line)
    return result, strip_ansi(renderer.render_to_text(result.renderable))


def test_visible_commands_are_productized_command_set() -> None:
    names = {spec.name for spec in visible_commands()}

    assert {
        "status",
        "usage",
        "memory",
        "permissions",
        "skills",
        "tasks",
        "mcp",
        "compact",
        "resume",
        "connect",
        "clear",
        "exit",
    } <= names
    assert {"help", "history", "tools", "trace", "background-tasks", "quit"}.isdisjoint(
        names
    )


def test_removed_user_commands_are_unknown(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    for command in (
        "/quit",
        "/help",
        "/history",
        "/tools",
        "/trace",
        "/background-tasks",
    ):
        result, output = run_command(runtime, command)
        assert result.should_exit is False
        assert "Unknown command" in output
        assert "Press Tab after /" in output


def test_banner_shows_only_product_workspace_and_model(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    output = strip_ansi(renderer.render_to_text(renderer.render_banner(runtime)))

    assert "OneCode" in output
    assert str(tmp_path) in output
    assert "test-model" in output
    assert "TestProvider" not in output
    assert "session-cli" not in output
    assert "workspace" not in output.lower()
    assert "model" not in output.lower().replace("test-model", "")


def test_read_only_status_commands_are_page_results(tmp_path: Path) -> None:
    runtime = replace(
        make_runtime(tmp_path),
        task_store=TaskStore(tmp_path),
        background_task_manager=BackgroundTaskManager(workspace=tmp_path),
        mcp_manager=FakeMcpManager(),  # type: ignore[arg-type]
    )

    for command in ("/status", "/usage", "/memory", "/permissions", "/skills", "/tasks", "/mcp"):
        result, output = run_command(runtime, command)
        assert result.presentation == "page"
        assert output


def test_status_command_shows_session_and_model(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    result, output = run_command(runtime, "/status")

    assert result.presentation == "page"
    assert "session-cli" in output
    assert "TestProvider" in output
    assert "test-model" in output
    assert ".onecode" in output


def test_usage_command_shows_tokens_and_compaction(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), compaction_service=FakeCompactionService())  # type: ignore[arg-type]

    _result, output = run_command(runtime, "/usage")

    assert "input tokens" in output
    assert "output tokens" in output
    assert "auto compact threshold" in output
    assert "93000" in output


def test_mcp_command_renders_server_status_and_tools(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), mcp_manager=FakeMcpManager())  # type: ignore[arg-type]

    _result, output = run_command(runtime, "/mcp")

    assert "MCP" in output
    assert "docs" in output
    assert "connected" in output
    assert "mcp__docs__search_docs" in output
    assert "readOnlyHint=True" in output


def test_mcp_command_renders_untrusted_server(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), mcp_manager=FakeUntrustedMcpManager())  # type: ignore[arg-type]

    _result, output = run_command(runtime, "/mcp")

    assert "docs" in output
    assert "untrusted" in output
    assert "none" in output


def test_status_command_counts_untrusted_mcp_servers(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), mcp_manager=FakeUntrustedMcpManager())  # type: ignore[arg-type]

    _result, output = run_command(runtime, "/status")

    assert "untrusted=1" in output


def test_compact_command_triggers_manual_compact(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    compaction = FakeCompactionService()
    runtime = replace(runtime, compaction_service=compaction)  # type: ignore[arg-type]

    result, output = run_command(runtime, "/compact current goal")

    assert result.should_exit is False
    assert compaction.focus == "current goal"
    assert "Compacted session" in output
    assert "100 -> 25" in output


def test_clear_command_starts_new_session_without_deleting_old(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    runtime.message_store.append_user("old")
    runtime.message_store.flush_transcript()
    old_session = runtime.state.session_id

    result, output = run_command(runtime, "/clear")
    runtime.message_store.append_user("new")
    runtime.message_store.flush_transcript()

    assert result.should_exit is False
    assert result.reset_main_view is True
    assert runtime.state.session_id != old_session
    assert runtime.message_store.current_messages() == (
        {"role": "user", "content": "new"},
    )
    assert session_messages_path(tmp_path, old_session).exists()
    assert "Started new session" in output


def test_clear_command_rebinds_session_scoped_services(tmp_path: Path) -> None:
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

    result, _output = run_command(runtime, "/clear")
    cleared = result.runtime

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


def test_unknown_command_does_not_exit(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)

    result, output = run_command(runtime, "/nope")

    assert result.should_exit is False
    assert "Unknown command" in output


def test_tasks_command_renders_empty_task_list_and_background_section(tmp_path: Path) -> None:
    runtime = replace(
        make_runtime(tmp_path),
        task_store=TaskStore(tmp_path),
        background_task_manager=BackgroundTaskManager(workspace=tmp_path),
    )

    result, output = run_command(runtime, "/tasks")

    assert result.should_exit is False
    assert "No tasks found for task list session-cli." in output
    assert "Background tasks: none" in output
    assert runtime.state.metadata["task_list_id"] == "session-cli"


def test_tasks_command_renders_existing_tasks(tmp_path: Path) -> None:
    task_store = TaskStore(tmp_path)
    first = task_store.create_task("session-cli", subject="Schema", description="A")
    second = task_store.create_task("session-cli", subject="API", description="B")
    task_store.block_task("session-cli", first.id, second.id)
    runtime = replace(make_runtime(tmp_path), task_store=task_store)

    _result, output = run_command(runtime, "/tasks")

    assert "Durable tasks" in output
    assert "task list: session-cli" in output
    assert ".onecode" in output
    assert "#1" in output
    assert "pending" in output
    assert "Schema" in output
    assert "#2" in output
    assert "API" in output
    assert "#1" in output


def test_tasks_command_reports_store_errors(tmp_path: Path) -> None:
    task_store = TaskStore(tmp_path)
    task_dir = task_store.tasks_dir("session-cli")
    task_dir.mkdir(parents=True)
    (task_dir / "1.json").write_text("{bad json", encoding="utf-8")
    runtime = replace(make_runtime(tmp_path), task_store=task_store)

    _result, output = run_command(runtime, "/tasks")

    assert "Could not read task file" in output


def test_tasks_command_renders_background_tasks(tmp_path: Path) -> None:
    async def start_task(manager: BackgroundTaskManager, state: RuntimeState) -> None:
        async def work(task_id: str) -> dict[str, object]:
            _ = task_id
            return {"summary": "done"}

        manager.start_agent(description="agent work", state=state, run=work)
        await asyncio.sleep(0)

    runtime = make_runtime(tmp_path)
    manager = BackgroundTaskManager(workspace=tmp_path)
    asyncio.run(start_task(manager, runtime.state))
    runtime = replace(
        runtime,
        task_store=TaskStore(tmp_path),
        background_task_manager=manager,
    )

    _result, output = run_command(runtime, "/tasks")

    assert "Background tasks" in output
    assert "local_agent" in output
    assert "completed" in output


def test_permissions_command_is_read_only(tmp_path: Path) -> None:
    store = SessionPermissionStore()
    store.allow_tool("bash")
    store.deny_tool("agent")
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(store, project_store=project_store)
    runtime = replace(
        make_runtime(tmp_path),
        permission_store=store,
        permission_policy=policy,
    )
    before = store.snapshot()

    _result, output = run_command(runtime, "/permissions")

    assert "Permissions" in output
    assert "bash" in output
    assert "agent" in output
    assert store.snapshot() == before
    assert not project_store.settings_path.exists()


def test_permissions_command_adds_project_rules(tmp_path: Path) -> None:
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(project_store=project_store)
    runtime = replace(make_runtime(tmp_path), permission_policy=policy)

    result, output = run_command(runtime, "/permissions add allow bash(npm run:*)")

    assert result.should_exit is False
    assert "Added project allow permission rule" in output
    settings = json.loads(project_store.settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == ["bash(npm run:*)"]


def test_permissions_command_adds_deny_rule(tmp_path: Path) -> None:
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(project_store=project_store)
    runtime = replace(make_runtime(tmp_path), permission_policy=policy)

    _result, _output = run_command(runtime, "/permissions add deny edit_file")

    settings = json.loads(project_store.settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["deny"] == ["edit_file"]


def test_permissions_command_removes_project_rules(tmp_path: Path) -> None:
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(project_store=project_store)
    runtime = replace(make_runtime(tmp_path), permission_policy=policy)
    run_command(runtime, "/permissions add allow bash(npm run:*)")

    result, output = run_command(runtime, "/permissions remove allow bash(npm run:*)")

    assert result.should_exit is False
    assert "Removed project allow permission rule" in output
    settings = json.loads(project_store.settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["allow"] == []


def test_permissions_command_replaces_project_rules(tmp_path: Path) -> None:
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(project_store=project_store)
    runtime = replace(make_runtime(tmp_path), permission_policy=policy)
    run_command(runtime, "/permissions add ask read_file(old/*)")

    result, output = run_command(runtime, "/permissions replace ask read_file(secret/*)")

    assert result.should_exit is False
    assert "Replaced project ask permission rule" in output
    settings = json.loads(project_store.settings_path.read_text(encoding="utf-8"))
    assert settings["permissions"]["ask"] == ["read_file(secret/*)"]


def test_permissions_command_rejects_invalid_project_rule_without_writing(
    tmp_path: Path,
) -> None:
    project_store = ProjectPermissionSettingsStore(tmp_path / ".onecode" / "settings.json")
    policy = PermissionPolicy(project_store=project_store)
    runtime = replace(make_runtime(tmp_path), permission_policy=policy)

    _result, output = run_command(runtime, "/permissions add allow bad(rule")

    assert "must end with an unescaped ')'" in output
    assert not project_store.settings_path.exists()


def test_permissions_command_requires_project_store(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), permission_policy=PermissionPolicy())

    _result, output = run_command(runtime, "/permissions add allow bash(npm run:*)")

    assert "Project permission settings are not enabled" in output


def test_memory_command_renders_session_and_long_term_state(tmp_path: Path) -> None:
    runtime = replace(
        make_runtime(tmp_path),
        session_memory_store=SessionMemoryStore(session_dir(tmp_path, "session-cli")),
    )

    _result, output = run_command(runtime, "/memory")

    assert "Memory" in output
    assert "session-memory.md" in output


def test_exit_command_flushes_and_exits(tmp_path: Path) -> None:
    runtime = replace(make_runtime(tmp_path), mcp_manager=FakeMcpManager())  # type: ignore[arg-type]
    runtime.message_store.append_user("bye")

    result, output = run_command(runtime, "/exit")

    assert result.should_exit is True
    assert output == ""
    assert runtime.message_store.transcript_store.messages_path.exists()
