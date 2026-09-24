"""会话列举、恢复目标解析与运行时恢复。

本模块是此前位于 ui.cli.resume 中的会话恢复业务在应用层的归属地。
它仅依赖 core/services 与应用运行时，供 batch 和 TUI 适配器共同复用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from application.runtime import ApplicationRuntime
from core.runtime_state import RuntimeState
from infrastructure.filesystem.onecode_paths import sessions_dir
from services.context.message_store import MessageStore
from services.context.transcript import VALID_MESSAGE_ROLES, JsonlTranscriptStore
from services.tools.file_state import FileStateCache

_PREVIEW_CHARS = 160


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    messages_path: Path
    title: str
    message_count: int
    updated_at: datetime | None


def list_session_summaries(workspace: Path) -> tuple[SessionSummary, ...]:
    root = sessions_dir(workspace)
    if not root.exists():
        return ()
    summaries = [
        summary
        for messages_path in root.glob("*/messages.jsonl")
        if (summary := summarize_session(messages_path)) is not None
    ]
    return tuple(
        sorted(
            summaries,
            key=lambda item: item.updated_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
    )


def summarize_session(messages_path: Path) -> SessionSummary | None:
    if not messages_path.is_file():
        return None
    session_id = messages_path.parent.name
    message_count = 0
    updated_at: datetime | None = None
    user_title: str | None = None
    assistant_title: str | None = None

    try:
        handle = messages_path.open("r", encoding="utf-8")
    except OSError:
        return None
    with handle:
        for line in handle:
            record = _parse_json_line(line)
            if record is None or record.get("type") != "message":
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("role") not in VALID_MESSAGE_ROLES:
                continue
            message_count += 1
            timestamp = _parse_timestamp(record.get("timestamp"))
            updated_at = timestamp or updated_at
            role = message.get("role")
            text = _message_text(message)
            if role == "user" and text and user_title is None:
                user_title = text
            elif role == "assistant" and text and assistant_title is None:
                assistant_title = text

    if message_count == 0:
        return None
    title = _truncate_title(user_title or assistant_title or session_id)
    return SessionSummary(
        session_id=session_id,
        messages_path=messages_path,
        title=title,
        message_count=message_count,
        updated_at=updated_at,
    )


def resolve_resume_target(workspace: Path, target: str) -> JsonlTranscriptStore:
    workspace = workspace.resolve()
    sessions_root = sessions_dir(workspace).resolve()
    target_path = Path(target).expanduser()
    if not target_path.is_absolute():
        target_path = workspace / target_path

    if target_path.suffix.lower() == ".jsonl" or target_path.is_file():
        messages_path = target_path
    else:
        messages_path = sessions_dir(workspace) / target / "messages.jsonl"
    messages_path = messages_path.resolve()
    _ensure_inside_sessions_root(messages_path, sessions_root)

    if not messages_path.exists():
        raise ValueError(f"Transcript does not exist: {messages_path}")
    if not messages_path.is_file():
        raise ValueError(f"Transcript target is not a file: {messages_path}")
    if messages_path.suffix.lower() != ".jsonl":
        raise ValueError(f"Transcript target must be a JSONL file: {messages_path}")

    session_dir = messages_path.parent
    return JsonlTranscriptStore(
        session_dir.parent,
        session_dir.name,
        cwd=workspace,
    )


def restore_runtime_from_target(
    runtime: ApplicationRuntime,
    target: str,
) -> ApplicationRuntime:
    transcript_store = resolve_resume_target(runtime.workspace, target)
    if not transcript_store.load_messages():
        raise ValueError(
            f"Transcript has no loadable messages: {transcript_store.messages_path}"
        )
    runtime.message_store.flush_transcript()
    state = RuntimeState(max_turns=runtime.state.max_turns)
    message_store = MessageStore.from_transcript(transcript_store, state)
    file_state_cache = restore_session_state(
        state,
        message_store.current_messages(),
    )
    return runtime.with_session(
        state=state,
        message_store=message_store,
        file_state_cache=file_state_cache,
    )


def restore_session_state(
    state: RuntimeState,
    messages: tuple[dict[str, Any], ...],
) -> FileStateCache:
    file_state_cache = FileStateCache()
    files_read: set[str] = set()
    files_changed: set[str] = set()

    for message in messages:
        if message.get("role") != "tool_result" or message.get("is_error") is True:
            continue
        tool_name = message.get("tool_name")
        if tool_name not in {"read_file", "edit_file", "write_file", "filewrite"}:
            continue
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            continue
        path_value = metadata.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue

        files_read.add(path_value)
        path = Path(path_value)
        if tool_name in {"edit_file", "write_file", "filewrite"}:
            files_changed.add(path_value)
            file_state_cache.snapshot_path(path, partial=False)
        elif tool_name == "read_file":
            offset = _int_or_none(metadata.get("offset"))
            file_state_cache.snapshot_path(path, offset=offset, partial=True)

    if files_read:
        state.metadata["files_read"] = files_read
    if files_changed:
        state.metadata["files_changed"] = files_changed
    return file_state_cache


def _parse_json_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _message_text(message: dict[str, Any]) -> str:
    return " ".join(_preview(message.get("content"), limit=120).split())


def _preview(value: Any, limit: int = _PREVIEW_CHARS) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = " ".join(_preview_block(block) for block in value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _preview_block(block: Any) -> str:
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str):
            return text
    return str(block)


def _truncate_title(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= 60:
        return text
    return text[:57] + "..."


def _ensure_inside_sessions_root(messages_path: Path, sessions_root: Path) -> None:
    try:
        messages_path.relative_to(sessions_root)
    except ValueError:
        raise ValueError(
            f"Resume target must be inside current workspace .onecode/sessions: {messages_path}"
        ) from None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


__all__ = [
    "SessionSummary",
    "list_session_summaries",
    "resolve_resume_target",
    "restore_runtime_from_target",
    "restore_session_state",
    "summarize_session",
]
