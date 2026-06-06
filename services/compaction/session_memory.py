"""Session-local Markdown memory for compaction continuity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core.runtime_state import RuntimeState
from services.compaction.token_estimator import estimate_messages_tokens
from services.observability import TraceRecorder
from services.subagents.types import SubagentRequest, SubagentResult


SESSION_MEMORY_EXTRACTION_KEY = "session_memory_extraction"


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


@dataclass(frozen=True)
class SessionMemoryExtractionPolicy:
    minimum_message_tokens_to_init: int = 10_000
    minimum_tokens_between_update: int = 5_000
    tool_calls_between_updates: int = 3


@dataclass(frozen=True)
class SessionMemoryExtractionDecision:
    should_extract: bool
    reason: str
    message_tokens: int
    tool_call_count: int
    token_delta: int
    tool_call_delta: int


class SessionMemorySubagentRunner(Protocol):
    async def run(self, request: SubagentRequest) -> SubagentResult: ...


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


class SessionMemoryExtractionService:
    """Use a restricted fork child to maintain session-local memory."""

    def __init__(
        self,
        store: SessionMemoryStore,
        *,
        subagent_runner: SessionMemorySubagentRunner,
        policy: SessionMemoryExtractionPolicy | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self._store = store
        self._subagent_runner = subagent_runner
        self._policy = policy or SessionMemoryExtractionPolicy()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._lock = asyncio.Lock()

    @property
    def store(self) -> SessionMemoryStore:
        return self._store

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None:
        """Run extraction when the message/tool growth threshold is reached."""

        _ = assistant_message, usage
        if state.metadata.get("query_source") == "compact":
            return
        if state.metadata.get("memory_extraction_agent") is True:
            return

        decision = should_extract_memory(
            messages,
            state,
            self._policy,
            last_response_had_tool_calls=bool(tool_calls),
        )
        _merge_extraction_metadata(
            state,
            {
                "path": str(self._store.path),
                "message_tokens": decision.message_tokens,
                "tool_call_count": decision.tool_call_count,
                "last_decision": decision.reason,
                "running": self.is_running,
            },
        )
        if not decision.should_extract:
            self._trace_decision("skipped", decision)
            return
        if self._lock.locked():
            _merge_extraction_metadata(
                state,
                {"last_status": "skipped_running", "running": True},
            )
            self._trace_decision("skipped_running", decision)
            return

        async with self._lock:
            current_decision = should_extract_memory(
                messages,
                state,
                self._policy,
                last_response_had_tool_calls=bool(tool_calls),
            )
            if not current_decision.should_extract:
                self._trace_decision("skipped_after_lock", current_decision)
                return
            started_at = _now()
            _merge_extraction_metadata(
                state,
                {
                    "last_status": "running",
                    "last_started_at": started_at,
                    "running": True,
                    "path": str(self._store.path),
                },
            )
            try:
                request = SubagentRequest(
                    prompt=_memory_extraction_prompt(
                        memory_path=self._store.path,
                        current_memory=self._store.read(),
                    ),
                    subagent_type=None,
                    parent_session_id=state.session_id,
                    parent_tool_call_id=f"session-memory-{state.turn_count}",
                    metadata={
                        "purpose": "session_memory_extraction",
                        "allowed_memory_path": str(self._store.path.resolve()),
                    },
                )
                result = await self._subagent_runner.run(request)
                if result.is_error:
                    raise RuntimeError(result.final_text)
                _merge_extraction_metadata(
                    state,
                    {
                        "last_status": "success",
                        "last_completed_at": _now(),
                        "last_extracted_token_count": current_decision.message_tokens,
                        "last_extracted_tool_call_count": (
                            current_decision.tool_call_count
                        ),
                        "last_result_session_id": result.session_id,
                        "running": False,
                        "resume_generation": _resume_generation(state),
                    },
                )
                state.metadata.pop("session_memory_resume_needs_extraction", None)
                self._trace_recorder.event(
                    "session_memory_extraction_completed",
                    {
                        "status": "success",
                        "path": self._store.path,
                        "message_tokens": current_decision.message_tokens,
                        "tool_call_count": current_decision.tool_call_count,
                        "child_session_id": result.session_id,
                    },
                )
            except Exception as exc:
                _merge_extraction_metadata(
                    state,
                    {
                        "last_status": "failed",
                        "last_completed_at": _now(),
                        "last_error_type": type(exc).__name__,
                        "running": False,
                    },
                )
                self._trace_recorder.event(
                    "session_memory_extraction_failed",
                    {
                        "error_type": type(exc).__name__,
                        "path": self._store.path,
                    },
                )

    async def wait_for_current_extraction(self, state: RuntimeState) -> None:
        """Wait for an in-flight extraction before compaction consumes memory."""

        if not self._lock.locked():
            return
        _merge_extraction_metadata(state, {"last_status": "waiting", "running": True})
        async with self._lock:
            _merge_extraction_metadata(state, {"running": False})

    def _trace_decision(
        self,
        status: str,
        decision: SessionMemoryExtractionDecision,
    ) -> None:
        self._trace_recorder.event(
            "session_memory_extraction_decision",
            {
                "status": status,
                "reason": decision.reason,
                "message_tokens": decision.message_tokens,
                "tool_call_count": decision.tool_call_count,
                "token_delta": decision.token_delta,
                "tool_call_delta": decision.tool_call_delta,
            },
        )


def should_extract_memory(
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
    policy: SessionMemoryExtractionPolicy,
    *,
    last_response_had_tool_calls: bool,
) -> SessionMemoryExtractionDecision:
    message_tokens = estimate_messages_tokens(messages)
    tool_call_count = count_tool_calls(messages)
    extraction_state = _extraction_metadata(state)
    last_tokens = _int_or_zero(extraction_state.get("last_extracted_token_count"))
    last_tool_calls = _int_or_zero(
        extraction_state.get("last_extracted_tool_call_count")
    )
    token_delta = message_tokens - last_tokens
    tool_call_delta = tool_call_count - last_tool_calls

    if message_tokens < policy.minimum_message_tokens_to_init:
        return SessionMemoryExtractionDecision(
            False,
            "below_initial_token_threshold",
            message_tokens,
            tool_call_count,
            token_delta,
            tool_call_delta,
        )
    if state.metadata.get("session_memory_resume_needs_extraction") is True:
        return SessionMemoryExtractionDecision(
            True,
            "resume_initial_extraction",
            message_tokens,
            tool_call_count,
            token_delta,
            tool_call_delta,
        )
    if token_delta < policy.minimum_tokens_between_update:
        return SessionMemoryExtractionDecision(
            False,
            "insufficient_token_delta",
            message_tokens,
            tool_call_count,
            token_delta,
            tool_call_delta,
        )
    if tool_call_delta >= policy.tool_calls_between_updates:
        return SessionMemoryExtractionDecision(
            True,
            "token_and_tool_growth",
            message_tokens,
            tool_call_count,
            token_delta,
            tool_call_delta,
        )
    if not last_response_had_tool_calls:
        return SessionMemoryExtractionDecision(
            True,
            "token_growth_after_text_response",
            message_tokens,
            tool_call_count,
            token_delta,
            tool_call_delta,
        )
    return SessionMemoryExtractionDecision(
        False,
        "insufficient_tool_call_delta",
        message_tokens,
        tool_call_count,
        token_delta,
        tool_call_delta,
    )


def count_tool_calls(messages: tuple[dict[str, Any], ...]) -> int:
    count = 0
    for message in messages:
        if message.get("role") != "assistant":
            continue
        count += _count_tool_calls_field(message.get("tool_calls"))
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for block in content if _is_tool_use_block(block))
    return count


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


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or "0")
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _extraction_metadata(state: RuntimeState) -> dict[str, Any]:
    value = state.metadata.get(SESSION_MEMORY_EXTRACTION_KEY)
    if isinstance(value, dict):
        return value
    value = {}
    state.metadata[SESSION_MEMORY_EXTRACTION_KEY] = value
    return value


def _merge_extraction_metadata(
    state: RuntimeState,
    updates: dict[str, Any],
) -> None:
    metadata = dict(_extraction_metadata(state))
    metadata.update(updates)
    state.metadata[SESSION_MEMORY_EXTRACTION_KEY] = metadata


def _resume_generation(state: RuntimeState) -> int:
    value = state.metadata.get("session_memory_resume_generation", 0)
    return _int_or_zero(value)


def _count_tool_calls_field(value: Any) -> int:
    if value in (None, "", (), []):
        return 0
    if isinstance(value, dict):
        return 1
    try:
        return sum(1 for _item in value)
    except TypeError:
        return 0


def _is_tool_use_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    block_type = block.get("type")
    return block_type in {"tool_use", "function_call"} and bool(
        block.get("name") or block.get("id") or block.get("tool_call_id")
    )


def _memory_extraction_prompt(
    *,
    memory_path: Path,
    current_memory: SessionMemory | None,
) -> str:
    current = current_memory.content if current_memory is not None else ""
    return "\n".join(
        [
            "Update the current session memory Markdown file.",
            "",
            f"Target file: {memory_path.resolve()}",
            "",
            "Rules:",
            "- Edit only the target file.",
            "- Preserve YAML front matter if it exists.",
            "- Keep the memory concise and useful for continuing this session.",
            "- Include these sections when useful: Current Goal, User Constraints, Key Findings, Relevant Files, Errors And Fixes, Pending Work, Next Step.",
            "- Do not write a long explanation to the parent. The file edit is the result.",
            "",
            "Current session memory:",
            current.strip() or "(missing)",
        ]
    )
