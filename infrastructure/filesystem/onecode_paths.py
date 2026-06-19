"""Workspace-local `.onecode` path helpers."""

from __future__ import annotations

from pathlib import Path


def onecode_dir(workspace: Path | str) -> Path:
    return Path(workspace) / ".onecode"


def sessions_dir(workspace: Path | str) -> Path:
    return onecode_dir(workspace) / "sessions"


def session_dir(workspace: Path | str, session_id: str) -> Path:
    return sessions_dir(workspace) / session_id


def session_messages_path(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "messages.jsonl"


def session_tool_results_dir(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "tool-results"


def session_background_tasks_dir(workspace: Path | str, session_id: str) -> Path:
    return session_dir(workspace, session_id) / "background-tasks"
