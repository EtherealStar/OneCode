"""Task record model and JSON projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, get_args

TaskStatus = Literal["pending", "in_progress", "completed"]
TASK_STATUSES = set(get_args(TaskStatus))


@dataclass(frozen=True)
class TaskRecord:
    id: str
    subject: str
    description: str
    active_form: str | None = None
    owner: str | None = None
    status: TaskStatus = "pending"
    blocks: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


def task_from_json(data: Mapping[str, Any]) -> TaskRecord:
    """Parse the durable camelCase task JSON shape into the Python model."""

    task_id = _required_string(data, "id")
    status = _required_string(data, "status")
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid task status for task {task_id}: {status}")
    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata must be an object for task {task_id}.")
    return TaskRecord(
        id=task_id,
        subject=_required_string(data, "subject"),
        description=_required_string(data, "description"),
        active_form=_optional_string(data, "activeForm"),
        owner=_optional_string(data, "owner"),
        status=status,  # type: ignore[arg-type]
        blocks=_string_tuple(data.get("blocks", ()), field_name="blocks", task_id=task_id),
        blocked_by=_string_tuple(
            data.get("blockedBy", ()),
            field_name="blockedBy",
            task_id=task_id,
        ),
        metadata=dict(metadata),
    )


def task_to_json(task: TaskRecord) -> dict[str, Any]:
    return {
        "id": task.id,
        "subject": task.subject,
        "description": task.description,
        "activeForm": task.active_form,
        "owner": task.owner,
        "status": task.status,
        "blocks": list(task.blocks),
        "blockedBy": list(task.blocked_by),
        "metadata": dict(task.metadata),
    }


def _required_string(data: Mapping[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value


def _optional_string(data: Mapping[str, Any], field_name: str) -> str | None:
    value = data.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null.")
    return value


def _string_tuple(value: Any, *, field_name: str, task_id: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list for task {task_id}.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise ValueError(f"{field_name} must contain only non-empty strings.")
        if item not in items:
            items.append(item)
    return tuple(items)
