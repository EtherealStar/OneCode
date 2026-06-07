"""Tool descriptor for stopping background tasks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.background_task_stop.prompt import PROMPT

if TYPE_CHECKING:
    from services.background_tasks import BackgroundTaskManager


class StopInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: str

    @field_validator("task_id")
    @classmethod
    def _strip_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("task_id must not be empty.")
        return stripped


INPUT_SCHEMA: dict[str, Any] = StopInput.model_json_schema()


def descriptor(background_task_manager: BackgroundTaskManager) -> ToolDescriptor:
    return ToolDescriptor(
        name="background_task_stop",
        description="Stop a running background bash, agent, or dream task.",
        input_schema=INPUT_SCHEMA,
        handler=_handler_for(background_task_manager),
        prompt=PROMPT,
        search_hint="stop background task",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    try:
        StopInput.model_validate(tool_input)
    except ValidationError as exc:
        return ValidationResult.failure(_validation_message(exc))
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    task_id = str(tool_input.get("task_id", ""))
    return ToolCallClassification(
        read_only=False,
        modifies_filesystem=False,
        concurrency_safe=False,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="background_task_stop",
                value=task_id,
            ),
        ),
        permission_subject=f"background_task_stop:{task_id}",
    )


def _handler_for(background_task_manager: BackgroundTaskManager):
    def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        parsed = StopInput.model_validate(tool_input)
        task = background_task_manager.stop(parsed.task_id)
        if task is None:
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="background_task_stop",
                content=json.dumps(
                    {
                        "error": "background_task_not_found",
                        "task_id": parsed.task_id,
                    },
                    ensure_ascii=False,
                ),
                is_error=True,
                metadata={"error": "background_task_not_found", "task_id": parsed.task_id},
            )
        payload = {
            "task_id": task.id,
            "task_type": task.type,
            "status": task.status,
            "output_file": task.output_file,
        }
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="background_task_stop",
            content=json.dumps(payload, ensure_ascii=False),
            metadata=payload,
        )

    return handle


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
