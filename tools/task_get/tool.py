"""Read one durable task record."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.tasks import TaskStore, TaskStoreError, resolve_task_list_id, task_to_json
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.task_get.prompt import PROMPT


class TaskGetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    task_id: str = Field(alias="taskId")

    @field_validator("task_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("taskId must not be empty.")
        return value


INPUT_SCHEMA: dict[str, Any] = TaskGetInput.model_json_schema(by_alias=True)


def descriptor(task_store: TaskStore) -> ToolDescriptor:
    def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return _handle(tool_input, runtime, task_store)

    return ToolDescriptor(
        name="task_get",
        description="Get full details for one durable task.",
        input_schema=INPUT_SCHEMA,
        handler=handle,
        prompt=PROMPT,
        search_hint="read durable task details",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> TaskGetInput:
    return TaskGetInput.model_validate(tool_input)


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
        result_policy=ToolResultPolicy(max_result_size_chars=20_000),
        permission_subject=f"task_get:{task_list_id}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    task_store: TaskStore,
) -> ToolExecutionResult:
    parsed = _parse_input(tool_input)
    task_list_id = resolve_task_list_id(runtime.state)
    try:
        task = task_store.get_task(task_list_id, parsed.task_id)
    except TaskStoreError as exc:
        return _error(runtime, "task_store_error", str(exc), task_list_id=task_list_id)
    if task is None:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="task_get",
            content=f"Task #{parsed.task_id} not found.",
            metadata={
                "task_id": parsed.task_id,
                "task_list_id": task_list_id,
                "found": False,
            },
        )
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_get",
        content=json.dumps(task_to_json(task), ensure_ascii=False, indent=2),
        metadata={
            "task_id": task.id,
            "task_list_id": task_list_id,
            "found": True,
        },
    )


def _error(
    runtime: ToolRuntime,
    error: str,
    message: str,
    *,
    task_list_id: str,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_get",
        content=message,
        is_error=True,
        metadata={"error": error, "task_list_id": task_list_id},
    )


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
