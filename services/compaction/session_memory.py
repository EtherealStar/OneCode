"""Session-local Markdown memory for compaction continuity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.compaction.token_estimator import estimate_messages_tokens
from services.observability import TraceRecorder


@dataclass(frozen=True)
class SessionMemory:
    content: str
    last_summarized_message_uuid: str = ""
    updated_at: str = ""
    covered_turn_count: int = 0
    source: str = "rule"

    @property
    def is_empty(self) -> bool:
        body = _strip_front_matter(self.content).strip()
        return not body or body == "# Session Memory"


class SessionMemoryStore:
    """Read and write `.onecode/<session_id>/session-memory.md` only."""

    def __init__(self, session_dir: Path | str) -> None:
        self._session_dir = Path(session_dir)

    @property
    def path(self) -> Path:
        return self._session_dir / "session-memory.md"

    def read(self) -> SessionMemory | None:
        if not self.path.exists():
            return None
        content = self.path.read_text(encoding="utf-8")
        metadata = _parse_front_matter(content)
        return SessionMemory(
            content=content,
            last_summarized_message_uuid=metadata.get("last_summarized_message_uuid", ""),
            updated_at=metadata.get("updated_at", ""),
            covered_turn_count=_int_or_zero(metadata.get("covered_turn_count")),
            source=metadata.get("source", "rule"),
        )

    def write(self, memory: SessionMemory) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(_render_memory(memory), encoding="utf-8")


class SessionMemoryUpdater:
    """Rule-based first version of per-turn session memory updates."""

    def __init__(
        self,
        store: SessionMemoryStore,
        *,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self._store = store
        self._trace_recorder = trace_recorder or TraceRecorder.noop()

    @property
    def store(self) -> SessionMemoryStore:
        return self._store

    async def update_after_turn(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> None:
        if state.metadata.get("query_source") == "compact":
            return
        try:
            memory = build_rule_based_memory(messages, state)
            self._store.write(memory)
            state.metadata["session_memory"] = {
                "path": str(self._store.path),
                "updated_at": memory.updated_at,
                "covered_turn_count": memory.covered_turn_count,
                "last_summarized_message_uuid": memory.last_summarized_message_uuid,
            }
            self._trace_recorder.event(
                "session_memory_update",
                {
                    "status": "success",
                    "path": self._store.path,
                    "covered_turn_count": memory.covered_turn_count,
                    "estimated_tokens": estimate_messages_tokens(messages),
                },
            )
        except Exception as exc:
            self._trace_recorder.event(
                "session_memory_update",
                {"status": "failed", "error_type": type(exc).__name__},
            )


def build_rule_based_memory(
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
) -> SessionMemory:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    last_uuid = _message_uuid(messages[-1], len(messages) - 1) if messages else ""
    user_messages = [_text_content(message.get("content")) for message in messages if message.get("role") == "user"]
    assistant_messages = [
        _text_content(message.get("content"))
        for message in messages
        if message.get("role") == "assistant"
    ]
    tool_results = [
        message
        for message in messages
        if message.get("role") == "tool_result"
    ]
    files_read = sorted(str(path) for path in state.metadata.get("files_read", set()))
    files_changed = sorted(str(path) for path in state.metadata.get("files_changed", set()))
    errors = [
        f"{message.get('tool_name', 'tool')} {message.get('tool_call_id', '')}: {_preview(message.get('content'))}"
        for message in tool_results
        if message.get("is_error") is True
    ]
    body = "\n".join(
        [
            "# Session Memory",
            "",
            "## Current Goal",
            _last_nonempty(user_messages) or "Not yet established.",
            "",
            "## User Constraints",
            _bullet_lines(_recent_nonempty(user_messages, 5)),
            "",
            "## Key Findings",
            _bullet_lines(_recent_nonempty(assistant_messages, 5)),
            "",
            "## Files Read",
            _bullet_lines(files_read) if files_read else "- None recorded.",
            "",
            "## Files Changed",
            _bullet_lines(files_changed) if files_changed else "- None recorded.",
            "",
            "## Errors And Fixes",
            _bullet_lines(errors) if errors else "- None recorded.",
            "",
            "## Pending Work",
            "- Continue from the latest user request and preserved recent messages.",
            "",
            "## Next Step",
            _last_nonempty(user_messages) or "Wait for the next user request.",
            "",
        ]
    )
    return SessionMemory(
        content=body,
        last_summarized_message_uuid=last_uuid,
        updated_at=now,
        covered_turn_count=state.turn_count,
        source="rule",
    )


def _render_memory(memory: SessionMemory) -> str:
    body = _strip_front_matter(memory.content).lstrip()
    return "\n".join(
        [
            "---",
            f"last_summarized_message_uuid: {memory.last_summarized_message_uuid}",
            f"updated_at: {memory.updated_at}",
            f"covered_turn_count: {memory.covered_turn_count}",
            f"source: {memory.source}",
            "---",
            body,
        ]
    )


def _parse_front_matter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip()
    return metadata


def _strip_front_matter(content: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return content
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[index + 1 :])
    return content


def _message_uuid(message: dict[str, Any], index: int) -> str:
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("message_uuid")
        if isinstance(value, str) and value:
            return value
    return f"message-{index + 1}"


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return "" if content is None else str(content)


def _preview(value: Any, limit: int = 160) -> str:
    text = " ".join(_text_content(value).split())
    return text if len(text) <= limit else f"{text[:limit]}..."


def _recent_nonempty(values: list[str], limit: int) -> list[str]:
    return [value for value in values if value.strip()][-limit:]


def _last_nonempty(values: list[str]) -> str:
    recent = _recent_nonempty(values, 1)
    return recent[0] if recent else ""


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None recorded."
    return "\n".join(f"- {_preview(value)}" for value in values)


def _int_or_zero(value: str | None) -> int:
    try:
        return int(value or "0")
    except ValueError:
        return 0
