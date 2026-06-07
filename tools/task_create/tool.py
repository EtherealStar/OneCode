"""Create durable task records."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.hooks import HookEvent, HookRegistry
from services.tasks import TaskStore, TaskStoreError, resolve_task_list_id
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.task_create.prompt import PROMPT


class TaskCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    subject: str
    description: str
    active_form: str | None = Field(default=None, alias="activeForm")
    metadata: dict[str, Any] | None = None

    @field_validator("subject", "description", "active_form")
    @classmethod
    def _non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty.")
        return value


INPUT_SCHEMA: dict[str, Any] = TaskCreateInput.model_json_schema(by_alias=True)


def descriptor(
    task_store: TaskStore,
    hooks: HookRegistry | None = None,
) -> ToolDescriptor:
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return await _handle(tool_input, runtime, task_store, hooks)

    return ToolDescriptor(
        name="task_create",
        description="Create a durable task in the current task list.",
        input_schema=INPUT_SCHEMA,
        handler=handle,
        prompt=PROMPT,
        search_hint="create durable task records",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> TaskCreateInput:
    return TaskCreateInput.model_validate(tool_input)


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    try:
        _parse_input(tool_input)
    except ValidationError as exc:
        return ValidationResult.failure(_validation_message(exc))
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    task_list_id = resolve_task_list_id(runtime.state)
    return ToolCallClassification(
        read_only=False,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="task_write",
                value=task_list_id,
            ),
        ),
        result_policy=ToolResultPolicy(max_result_size_chars=20_000),
        permission_subject=f"task_create:{task_list_id}",
    )


async def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    task_store: TaskStore,
    hooks: HookRegistry | None,
) -> ToolExecutionResult:
    parsed = _parse_input(tool_input)
    task_list_id = resolve_task_list_id(runtime.state)
    try:
        task = task_store.create_task(
            task_list_id,
            subject=parsed.subject,
            description=parsed.description,
            active_form=parsed.active_form,
            metadata=parsed.metadata,
        )
    except TaskStoreError as exc:
        return _error(runtime, "task_store_error", str(exc), task_list_id=task_list_id)

    if hooks is not None:
        hook_result = await hooks.run(
            HookEvent.TASK_CREATED,
            {
                "task_list_id": task_list_id,
                "task": task,
                "state": runtime.state,
                "workspace": task_store.workspace,
                "tool_call_id": runtime.tool_call_id,
                "event_source": "task_create",
            },
        )
        if hook_result.blocking_error is not None:
            task_store.delete_task(task_list_id, task.id)
            return _error(
                runtime,
                "task_created_hook_blocked",
                hook_result.blocking_error,
                task_list_id=task_list_id,
                task_id=task.id,
            )

    task_path = task_store.task_path(task_list_id, task.id)
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_create",
        content=f"Task #{task.id} created successfully: {task.subject}",
        metadata={
            "task_id": task.id,
            "task_list_id": task_list_id,
            "task_path": str(task_path),
            "task_relative_path": _relative(task_path, task_store.workspace),
        },
    )


def _error(
    runtime: ToolRuntime,
    error: str,
    message: str,
    *,
    task_list_id: str | None = None,
    task_id: str | None = None,
) -> ToolExecutionResult:
    metadata = {"error": error}
    if task_list_id is not None:
        metadata["task_list_id"] = task_list_id
    if task_id is not None:
        metadata["task_id"] = task_id
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_create",
        content=message,
        is_error=True,
        metadata=metadata,
    )


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
