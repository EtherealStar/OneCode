"""File-backed task store."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
from typing import Any, Iterable
import uuid

from services.tasks.types import TaskRecord, TaskStatus, task_from_json, task_to_json


class TaskStoreError(Exception):
    """Raised when the task graph cannot be read or safely updated."""


@dataclass(frozen=True)
class TaskUpdate:
    subject: str | None = None
    description: str | None = None
    active_form: str | None = None
    clear_active_form: bool = False
    owner: str | None = None
    clear_owner: bool = False
    status: TaskStatus | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class TaskClaimResult:
    task: TaskRecord | None
    claimed: bool
    reason: str | None = None


class TaskStore:
    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace)
        self.root = self.workspace / ".onecode" / "tasks"
        self._lock = threading.RLock()

    def tasks_dir(self, task_list_id: str) -> Path:
        return self.root / task_list_id

    def task_path(self, task_list_id: str, task_id: str) -> Path:
        return self.tasks_dir(task_list_id) / f"{task_id}.json"

    def create_task(
        self,
        task_list_id: str,
        *,
        subject: str,
        description: str,
        active_form: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskRecord:
        with self._lock:
            task_id = self._next_task_id(task_list_id)
            task = TaskRecord(
                id=task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                metadata=dict(metadata or {}),
            )
            self._write_task(task_list_id, task)
            return task

    def get_task(self, task_list_id: str, task_id: str) -> TaskRecord | None:
        path = self.task_path(task_list_id, task_id)
        if not path.exists():
            return None
        return self._read_task_path(path)

    def list_tasks(self, task_list_id: str) -> tuple[TaskRecord, ...]:
        directory = self.tasks_dir(task_list_id)
        if not directory.exists():
            return ()
        tasks = [
            self._read_task_path(path)
            for path in directory.glob("*.json")
            if not path.name.startswith(".") and not path.name.endswith(".tmp")
        ]
        return tuple(sorted(tasks, key=lambda task: _task_sort_key(task.id)))

    def update_task(
        self,
        task_list_id: str,
        task_id: str,
        updates: TaskUpdate,
    ) -> TaskRecord | None:
        with self._lock:
            task = self.get_task(task_list_id, task_id)
            if task is None:
                return None
            metadata = _merge_metadata(task.metadata, updates.metadata)
            updated = replace(
                task,
                subject=updates.subject if updates.subject is not None else task.subject,
                description=(
                    updates.description
                    if updates.description is not None
                    else task.description
                ),
                active_form=(
                    None
                    if updates.clear_active_form
                    else updates.active_form
                    if updates.active_form is not None
                    else task.active_form
                ),
                owner=(
                    None
                    if updates.clear_owner
                    else updates.owner
                    if updates.owner is not None
                    else task.owner
                ),
                status=updates.status if updates.status is not None else task.status,
                metadata=metadata,
            )
            self._write_task(task_list_id, updated)
            return updated

    def delete_task(self, task_list_id: str, task_id: str) -> bool:
        with self._lock:
            target = self.task_path(task_list_id, task_id)
            if not target.exists():
                return False
            target.unlink()
            for task in self.list_tasks(task_list_id):
                if task_id not in task.blocks and task_id not in task.blocked_by:
                    continue
                self._write_task(
                    task_list_id,
                    replace(
                        task,
                        blocks=tuple(item for item in task.blocks if item != task_id),
                        blocked_by=tuple(
                            item for item in task.blocked_by if item != task_id
                        ),
                    ),
                )
            return True

    def block_task(
        self,
        task_list_id: str,
        blocker_id: str,
        blocked_id: str,
    ) -> tuple[TaskRecord, TaskRecord]:
        with self._lock:
            if blocker_id == blocked_id:
                raise TaskStoreError("A task cannot block itself.")
            blocker = self.get_task(task_list_id, blocker_id)
            blocked = self.get_task(task_list_id, blocked_id)
            if blocker is None:
                raise TaskStoreError(f"Task not found: {blocker_id}")
            if blocked is None:
                raise TaskStoreError(f"Task not found: {blocked_id}")
            if blocked_id in blocker.blocks and blocker_id in blocked.blocked_by:
                return blocker, blocked
            if self._would_create_cycle(task_list_id, blocker_id, blocked_id):
                raise TaskStoreError(
                    f"Dependency would create a cycle: {blocker_id} -> {blocked_id}"
                )
            updated_blocker = replace(
                blocker,
                blocks=_sorted_ids((*blocker.blocks, blocked_id)),
            )
            updated_blocked = replace(
                blocked,
                blocked_by=_sorted_ids((*blocked.blocked_by, blocker_id)),
            )
            self._write_task(task_list_id, updated_blocker)
            self._write_task(task_list_id, updated_blocked)
            return updated_blocker, updated_blocked

    def claim_task(
        self,
        task_list_id: str,
        task_id: str,
        owner: str,
    ) -> TaskClaimResult:
        with self._lock:
            task = self.get_task(task_list_id, task_id)
            if task is None:
                return TaskClaimResult(None, claimed=False, reason="not_found")
            if task.status == "completed":
                return TaskClaimResult(task, claimed=False, reason="completed")
            if task.owner and task.owner != owner:
                return TaskClaimResult(task, claimed=False, reason="claimed")
            unfinished = self._unfinished_blockers(task_list_id, task)
            if unfinished:
                return TaskClaimResult(task, claimed=False, reason="blocked")
            updated = replace(task, owner=owner)
            self._write_task(task_list_id, updated)
            return TaskClaimResult(updated, claimed=True)

    def _next_task_id(self, task_list_id: str) -> str:
        directory = self.tasks_dir(task_list_id)
        directory.mkdir(parents=True, exist_ok=True)
        highwatermark_path = directory / ".highwatermark"
        current = self._read_highwatermark(highwatermark_path)
        if current is None:
            current = _max_numeric_id(task.id for task in self.list_tasks(task_list_id))
        next_id = current + 1
        self._atomic_write_text(highwatermark_path, str(next_id))
        return str(next_id)

    def _read_highwatermark(self, path: Path) -> int | None:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8").strip()
        try:
            value = int(raw)
        except ValueError as exc:
            raise TaskStoreError(f"Invalid task highwatermark: {path}") from exc
        if value < 0:
            raise TaskStoreError(f"Invalid negative task highwatermark: {path}")
        return value

    def _read_task_path(self, path: Path) -> TaskRecord:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("task JSON root must be an object")
            return task_from_json(data)
        except Exception as exc:
            raise TaskStoreError(f"Could not read task file {path}: {exc}") from exc

    def _write_task(self, task_list_id: str, task: TaskRecord) -> None:
        path = self.task_path(task_list_id, task.id)
        payload = json.dumps(task_to_json(task), ensure_ascii=False, indent=2)
        self._atomic_write_text(path, f"{payload}\n")

    def _atomic_write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # The temp file lives beside the target so Path.replace() is atomic on
        # the same filesystem for normal local workspaces.
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _would_create_cycle(
        self,
        task_list_id: str,
        blocker_id: str,
        blocked_id: str,
    ) -> bool:
        tasks = {task.id: task for task in self.list_tasks(task_list_id)}
        stack = list(tasks[blocked_id].blocks) if blocked_id in tasks else []
        seen: set[str] = set()
        while stack:
            current = stack.pop()
            if current == blocker_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            task = tasks.get(current)
            if task is not None:
                stack.extend(task.blocks)
        return False

    def _unfinished_blockers(self, task_list_id: str, task: TaskRecord) -> tuple[str, ...]:
        tasks = {item.id: item for item in self.list_tasks(task_list_id)}
        return tuple(
            blocker_id
            for blocker_id in task.blocked_by
            if tasks.get(blocker_id) is not None
            and tasks[blocker_id].status != "completed"
        )


def _merge_metadata(
    existing: dict[str, Any],
    updates: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing)
    if updates is None:
        return merged
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def _max_numeric_id(values: Iterable[str]) -> int:
    maximum = 0
    for value in values:
        try:
            maximum = max(maximum, int(value))
        except ValueError:
            continue
    return maximum


def _sorted_ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values), key=_task_sort_key))


def _task_sort_key(task_id: str) -> tuple[int, int | str]:
    try:
        return (0, int(task_id))
    except ValueError:
        return (1, task_id)
