from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.runtime_state import RuntimeState
from services.hooks import HookEvent, HookRegistry, HookResult
from services.tasks import TaskStore, TaskUpdate
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolExecutionResult
from tools.task_create import descriptor as task_create_descriptor
from tools.task_get import descriptor as task_get_descriptor
from tools.task_list import descriptor as task_list_descriptor
from tools.task_update import descriptor as task_update_descriptor


def make_executor(
    tmp_path: Path,
    hooks: HookRegistry | None = None,
) -> tuple[RegistryToolExecutor, RuntimeState, TaskStore]:
    store = TaskStore(tmp_path)
    hooks = hooks or HookRegistry()
    registry = ToolRegistry(
        [
            task_create_descriptor(store, hooks),
            task_get_descriptor(store),
            task_update_descriptor(store, hooks),
            task_list_descriptor(store),
        ]
    )
    state = RuntimeState(session_id="task-session")
    return RegistryToolExecutor(registry, hooks=hooks), state, store


def execute_one(
    executor: RegistryToolExecutor,
    state: RuntimeState,
    tool_name: str,
    tool_input: dict[str, object],
) -> ToolExecutionResult:
    async def collect() -> list[ToolExecutionResult]:
        results: list[ToolExecutionResult] = []
        async for update in executor.execute(
            (ToolCall(id="call-1", name=tool_name, input=tool_input),),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        return results

    return asyncio.run(collect())[0]


def test_task_create_get_list_and_update_dependencies(tmp_path: Path) -> None:
    executor, state, store = make_executor(tmp_path)

    created = execute_one(
        executor,
        state,
        "task_create",
        {"subject": "Schema", "description": "Create schema."},
    )
    second = execute_one(
        executor,
        state,
        "task_create",
        {"subject": "API", "description": "Build API."},
    )
    updated = execute_one(
        executor,
        state,
        "task_update",
        {"taskId": "2", "status": "in_progress", "owner": "main", "addBlockedBy": ["1"]},
    )

    assert created.is_error is False
    assert created.metadata["task_id"] == "1"
    assert second.metadata["task_id"] == "2"
    assert updated.is_error is False
    assert "blockedBy #1" in updated.content
    assert store.get_task("task-session", "1").blocks == ("2",)
    assert store.get_task("task-session", "2").blocked_by == ("1",)

    listed = execute_one(executor, state, "task_list", {})
    assert "#1 [pending] Schema" in listed.content
    assert "#2 [in_progress] API owner=main [blocked by #1]" in listed.content

    got = execute_one(executor, state, "task_get", {"taskId": "2"})
    payload = json.loads(got.content)
    assert payload["blockedBy"] == ["1"]
    assert payload["owner"] == "main"


def test_task_list_hides_internal_tasks_and_completed_blockers(tmp_path: Path) -> None:
    executor, state, store = make_executor(tmp_path)
    first = store.create_task("task-session", subject="Schema", description="A")
    second = store.create_task("task-session", subject="API", description="B")
    store.create_task(
        "task-session",
        subject="Internal",
        description="Hidden",
        metadata={"_internal": True},
    )
    store.block_task("task-session", first.id, second.id)
    store.update_task("task-session", first.id, TaskUpdate(status="completed"))

    listed = execute_one(executor, state, "task_list", {})

    assert "Internal" not in listed.content
    assert "#2 [pending] API [blocked by #1]" not in listed.content
    assert "#2 [pending] API" in listed.content


def test_missing_task_get_and_delete_status_are_recoverable(tmp_path: Path) -> None:
    executor, state, store = make_executor(tmp_path)
    store.create_task("task-session", subject="One", description="A")

    missing = execute_one(executor, state, "task_get", {"taskId": "9"})
    deleted = execute_one(executor, state, "task_update", {"taskId": "1", "status": "deleted"})

    assert missing.is_error is False
    assert "not found" in missing.content
    assert deleted.is_error is False
    assert store.get_task("task-session", "1") is None


def test_task_created_hook_can_block_and_roll_back_file(tmp_path: Path) -> None:
    hooks = HookRegistry()
    hooks.register(
        HookEvent.TASK_CREATED,
        lambda payload: HookResult(blocking_error="task creation disabled"),
    )
    executor, state, store = make_executor(tmp_path, hooks)

    result = execute_one(
        executor,
        state,
        "task_create",
        {"subject": "Blocked", "description": "Should be removed."},
    )

    assert result.is_error is True
    assert "task creation disabled" in result.content
    assert store.list_tasks("task-session") == ()


def test_task_completed_hook_can_block_status_change(tmp_path: Path) -> None:
    hooks = HookRegistry()
    hooks.register(
        HookEvent.TASK_COMPLETED,
        lambda payload: HookResult(blocking_error="completion requires review"),
    )
    executor, state, store = make_executor(tmp_path, hooks)
    store.create_task("task-session", subject="Review", description="A")

    result = execute_one(
        executor,
        state,
        "task_update",
        {"taskId": "1", "status": "completed"},
    )

    assert result.is_error is True
    assert "completion requires review" in result.content
    assert store.get_task("task-session", "1").status == "pending"


def test_task_update_completed_result_suggests_checking_remaining_work(
    tmp_path: Path,
) -> None:
    executor, state, store = make_executor(tmp_path)
    store.create_task("task-session", subject="Review", description="A")

    completed = execute_one(
        executor,
        state,
        "task_update",
        {"taskId": "1", "status": "completed"},
    )
    repeated = execute_one(
        executor,
        state,
        "task_update",
        {"taskId": "1", "status": "completed"},
    )

    assert completed.is_error is False
    assert "Task completed. Use task_list" in completed.content
    assert repeated.is_error is False
    assert "Task completed. Use task_list" not in repeated.content
