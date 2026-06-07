"""List durable tasks."""

from __future__ import annotations

from typing import Any

from services.tasks import TaskRecord, TaskStore, TaskStoreError, resolve_task_list_id
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
)
from tools.task_list.prompt import PROMPT


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


def descriptor(task_store: TaskStore) -> ToolDescriptor:
    def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return _handle(tool_input, runtime, task_store)

    return ToolDescriptor(
        name="task_list",
        description="List durable tasks in the current task list.",
        input_schema=INPUT_SCHEMA,
        handler=handle,
        prompt=PROMPT,
        search_hint="list durable tasks",
        classify_input=_classify_input,
    )


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    task_list_id = resolve_task_list_id(runtime.state)
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="task_read",
                value=task_list_id,
            ),
        ),
        result_policy=ToolResultPolicy(max_result_size_chars=100_000),
        permission_subject=f"task_list:{task_list_id}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    task_store: TaskStore,
) -> ToolExecutionResult:
    task_list_id = resolve_task_list_id(runtime.state)
    try:
        tasks = _visible_tasks(task_store.list_tasks(task_list_id))
    except TaskStoreError as exc:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="task_list",
            content=str(exc),
            is_error=True,
            metadata={"error": "task_store_error", "task_list_id": task_list_id},
        )
    content = format_task_list(tasks)
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_list",
        content=content,
        metadata={
            "task_list_id": task_list_id,
            "task_count": len(tasks),
        },
    )


def format_task_list(tasks: tuple[TaskRecord, ...]) -> str:
    if not tasks:
        return "No tasks found."
    by_id = {task.id: task for task in tasks}
    lines = ["Tasks:"]
    for task in tasks:
        suffix = ""
        unfinished = [
            task_id
            for task_id in task.blocked_by
            if by_id.get(task_id) is not None and by_id[task_id].status != "completed"
        ]
        if task.owner:
            suffix += f" owner={task.owner}"
        if unfinished:
            suffix += " [blocked by " + ", ".join(f"#{task_id}" for task_id in unfinished) + "]"
        lines.append(f"  #{task.id} [{task.status}] {task.subject}{suffix}")
    return "\n".join(lines)


def _visible_tasks(tasks: tuple[TaskRecord, ...]) -> tuple[TaskRecord, ...]:
    return tuple(task for task in tasks if task.metadata.get("_internal") is not True)
