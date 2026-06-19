"""Update durable task records."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.hooks import HookEvent, HookRegistry
from services.tasks import TaskStore, TaskStoreError, TaskUpdate, resolve_task_list_id
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.task_update.prompt import PROMPT


class TaskUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    task_id: str = Field(alias="taskId")
    subject: str | None = None
    description: str | None = None
    active_form: str | None = Field(default=None, alias="activeForm")
    status: Literal["pending", "in_progress", "completed", "deleted"] | None = None
    owner: str | None = None
    add_blocks: list[str] | None = Field(default=None, alias="addBlocks")
    add_blocked_by: list[str] | None = Field(default=None, alias="addBlockedBy")
    metadata: dict[str, Any] | None = None

    @field_validator("task_id", "subject", "description", "active_form", "owner")
    @classmethod
    def _non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty.")
        return value

    @field_validator("add_blocks", "add_blocked_by")
    @classmethod
    def _non_empty_list(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("task ids must not be empty.")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned


INPUT_SCHEMA: dict[str, Any] = TaskUpdateInput.model_json_schema(by_alias=True)


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
        name="task_update",
        description="Update a durable task, dependencies, owner, status, or metadata.",
        input_schema=INPUT_SCHEMA,
        handler=handle,
        prompt=PROMPT,
        search_hint="update durable task records and dependencies",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> TaskUpdateInput:
    return TaskUpdateInput.model_validate(tool_input)


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
        permission_subject=f"task_update:{task_list_id}",
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
        existing = task_store.get_task(task_list_id, parsed.task_id)
    except TaskStoreError as exc:
        return _error(runtime, "task_store_error", str(exc), task_list_id=task_list_id)
    if existing is None:
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="task_update",
            content=f"Task #{parsed.task_id} not found.",
            metadata={
                "task_id": parsed.task_id,
                "task_list_id": task_list_id,
                "found": False,
            },
        )

    if parsed.status == "deleted":
        deleted = task_store.delete_task(task_list_id, parsed.task_id)
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name="task_update",
            content=f"Task #{parsed.task_id} deleted." if deleted else f"Task #{parsed.task_id} not found.",
            metadata={
                "task_id": parsed.task_id,
                "task_list_id": task_list_id,
                "deleted": deleted,
            },
        )

    if parsed.status == "completed" and existing.status != "completed" and hooks is not None:
        hook_result = await hooks.run(
            HookEvent.TASK_COMPLETED,
            {
                "task_list_id": task_list_id,
                "task": existing,
                "state": runtime.state,
                "workspace": task_store.workspace,
                "tool_call_id": runtime.tool_call_id,
                "event_source": "task_update",
            },
        )
        if hook_result.blocking_error is not None:
            return _error(
                runtime,
                "task_completed_hook_blocked",
                hook_result.blocking_error,
                task_list_id=task_list_id,
                task_id=parsed.task_id,
            )

    update = TaskUpdate(
        subject=parsed.subject,
        description=parsed.description,
        active_form=parsed.active_form,
        clear_active_form="activeForm" in parsed.model_fields_set
        and parsed.active_form is None,
        status=parsed.status,
        owner=parsed.owner,
        clear_owner="owner" in parsed.model_fields_set and parsed.owner is None,
        metadata=parsed.metadata,
    )
    try:
        updated = task_store.update_task(task_list_id, parsed.task_id, update)
        assert updated is not None
        dependency_changes: list[str] = []
        for blocked_id in parsed.add_blocks or ():
            task_store.block_task(task_list_id, parsed.task_id, blocked_id)
            dependency_changes.append(f"blocks #{blocked_id}")
        for blocker_id in parsed.add_blocked_by or ():
            task_store.block_task(task_list_id, blocker_id, parsed.task_id)
            dependency_changes.append(f"blockedBy #{blocker_id}")
        updated = task_store.get_task(task_list_id, parsed.task_id)
        assert updated is not None
    except TaskStoreError as exc:
        return _error(
            runtime,
            "task_store_error",
            str(exc),
            task_list_id=task_list_id,
            task_id=parsed.task_id,
        )

    changed = _changed_fields(parsed, dependency_changes)
    content = f"Task #{updated.id} updated: {', '.join(changed) if changed else 'no changes'}"
    if parsed.status == "completed" and existing.status != "completed":
        content += (
            "\nTask completed. Use task_list if you need to check remaining or newly unblocked work."
        )
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_update",
        content=content,
        metadata={
            "task_id": updated.id,
            "task_list_id": task_list_id,
            "updated_fields": changed,
            "status": updated.status,
            "found": True,
        },
    )


def _changed_fields(parsed: TaskUpdateInput, dependency_changes: list[str]) -> list[str]:
    fields: list[str] = []
    alias_by_field = {
        "task_id": "taskId",
        "active_form": "activeForm",
        "add_blocks": "addBlocks",
        "add_blocked_by": "addBlockedBy",
    }
    for field_name in parsed.model_fields_set:
        if field_name in {"task_id", "add_blocks", "add_blocked_by"}:
            continue
        fields.append(alias_by_field.get(field_name, field_name))
    fields.extend(dependency_changes)
    return fields


def _error(
    runtime: ToolRuntime,
    error: str,
    message: str,
    *,
    task_list_id: str,
    task_id: str | None = None,
) -> ToolExecutionResult:
    metadata = {"error": error, "task_list_id": task_list_id}
    if task_id is not None:
        metadata["task_id"] = task_id
    return ToolExecutionResult(
        tool_call_id=runtime.tool_call_id,
        tool_name="task_update",
        content=message,
        is_error=True,
        metadata=metadata,
    )


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
