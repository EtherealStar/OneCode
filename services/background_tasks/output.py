"""Background task output file paths."""

from __future__ import annotations

from pathlib import Path


def background_task_output_dir(workspace: Path | str, session_id: str) -> Path:
    return Path(workspace) / ".onecode" / session_id / "background-tasks"


def background_task_output_path(
    workspace: Path | str,
    session_id: str,
    task_id: str,
) -> Path:
    return background_task_output_dir(workspace, session_id) / f"{task_id}.output"
