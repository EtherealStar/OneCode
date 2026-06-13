"""Durable and background task views."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from rich.console import Group
from rich.table import Table
from rich.text import Text

from services.background_tasks import BackgroundTaskState
from services.tasks import TaskRecord
from ui.cli.theme import SYMBOLS
from ui.cli.types import CliRuntime
from ui.cli.views.common import display_path, titled_section


def render_tasks(
    runtime: CliRuntime,
    tasks: Iterable[TaskRecord],
    *,
    task_list_id: str | None,
    tasks_dir: Path | None,
    background_tasks: Iterable[BackgroundTaskState] = (),
    durable_error: str | None = None,
) -> Group:
    durable = _durable_tasks_table(
        runtime,
        tuple(tasks),
        task_list_id=task_list_id,
        tasks_dir=tasks_dir,
        durable_error=durable_error,
    )
    background = _background_tasks_table(runtime, tuple(background_tasks))
    return titled_section("Tasks", Group(durable, Text(), background), style="onecode.info")


def _durable_tasks_table(
    runtime: CliRuntime,
    tasks: tuple[TaskRecord, ...],
    *,
    task_list_id: str | None,
    tasks_dir: Path | None,
    durable_error: str | None,
) -> object:
    if durable_error is not None:
        return Text(f"{SYMBOLS.error} Durable tasks: {durable_error}", style="onecode.error")
    if task_list_id is None:
        return Text(f"{SYMBOLS.info} Durable tasks: disabled", style="onecode.subtle")
    items = [task for task in tasks if task.metadata.get("_internal") is not True]
    table = Table(title="Durable tasks", box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("id", no_wrap=True)
    table.add_column("status")
    table.add_column("subject")
    table.add_column("owner")
    table.add_column("blocked by")
    if tasks_dir is not None:
        table.caption = (
            f"task list: {task_list_id}   path: {display_path(tasks_dir, runtime.workspace)}"
        )
    if not items:
        table.add_row("-", "none", f"No tasks found for task list {task_list_id}.", "", "")
        return table
    by_id = {task.id: task for task in items}
    for task in items:
        unfinished_blockers = [
            blocker_id
            for blocker_id in task.blocked_by
            if by_id.get(blocker_id) is not None
            and by_id[blocker_id].status != "completed"
        ]
        table.add_row(
            f"#{task.id}",
            task.status,
            task.subject,
            task.owner or "",
            ", ".join(f"#{item}" for item in unfinished_blockers),
        )
    return table


def _background_tasks_table(
    runtime: CliRuntime,
    tasks: tuple[BackgroundTaskState, ...],
) -> object:
    table = Table(title="Background tasks", box=None, show_header=True, header_style="onecode.subtle")
    table.add_column("id")
    table.add_column("type")
    table.add_column("status")
    table.add_column("description")
    table.add_column("output")
    table.add_column("detail")
    if not tasks:
        table.add_row("-", "none", "none", "Background tasks: none", "", "")
        return table
    for task in tasks[-20:]:
        table.add_row(
            task.id,
            task.type,
            task.status,
            task.description,
            display_path(Path(task.output_file), runtime.workspace),
            _background_task_detail(task),
        )
    return table


def _background_task_detail(task: BackgroundTaskState) -> str:
    if task.type == "local_bash":
        parts = []
        if "exit_code" in task.metadata:
            parts.append(f"exit_code={task.metadata.get('exit_code')}")
        if task.metadata.get("timed_out") is True:
            parts.append("timed_out=true")
        return " ".join(parts)
    if task.type == "local_agent":
        child = task.metadata.get("child_session_id")
        return f"child_session_id={child}" if child else ""
    if task.type == "dream":
        child = task.metadata.get("result_session_id")
        return f"result_session_id={child}" if child else ""
    return ""
