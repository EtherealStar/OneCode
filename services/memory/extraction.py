"""Blocked first-version long-term memory extraction via restricted fork child."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core.runtime_state import RuntimeState
from services.memory.auto_store import LongTermMemoryStore
from services.subagents.types import SubagentRequest, SubagentResult
from services.observability import TraceRecorder

LONG_TERM_MEMORY_EXTRACTION_KEY = "long_term_memory_extraction"


@dataclass(frozen=True)
class LongTermMemoryExtractionPolicy:
    enabled: bool = True
    max_turns: int = 5


class LongTermMemorySubagentRunner(Protocol):
    async def run(self, request: SubagentRequest) -> SubagentResult: ...


class LongTermMemoryExtractionService:
    def __init__(
        self,
        store: LongTermMemoryStore,
        *,
        subagent_runner: LongTermMemorySubagentRunner,
        policy: LongTermMemoryExtractionPolicy | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.store = store
        self._subagent_runner = subagent_runner
        self._policy = policy or LongTermMemoryExtractionPolicy()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._lock = asyncio.Lock()

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
        _ = assistant_message, usage
        decision = should_extract_long_term_memory(
            messages,
            state,
            tool_calls=tool_calls,
            enabled=self._policy.enabled,
        )
        _merge_metadata(
            state,
            {
                "last_decision": decision,
                "running": self.is_running,
                "memory_dir": str(self.store.memory_dir),
            },
        )
        if decision != "extract":
            self._trace_recorder.event(
                "long_term_memory_extraction_decision",
                {"status": "skipped", "reason": decision},
            )
            if decision == "main_agent_memory_write":
                _advance_cursor(messages, state)
            return
        if self._lock.locked():
            _merge_metadata(state, {"last_status": "skipped_running", "running": True})
            return
        async with self._lock:
            _merge_metadata(
                state,
                {
                    "last_status": "running",
                    "last_started_at": _now(),
                    "running": True,
                },
            )
            try:
                self.store.ensure_exists()
                request = SubagentRequest(
                    prompt=_extraction_prompt(self.store, messages, state),
                    subagent_type=None,
                    parent_session_id=state.session_id,
                    parent_tool_call_id=f"long-term-memory-{state.turn_count}",
                    metadata={
                        "purpose": "long_term_memory_extraction",
                        "allowed_memory_dir": str(self.store.memory_dir.resolve()),
                        "max_turns": self._policy.max_turns,
                    },
                )
                result = await self._subagent_runner.run(request)
                if result.is_error:
                    raise RuntimeError(result.final_text)
                _advance_cursor(messages, state)
                _merge_metadata(
                    state,
                    {
                        "last_status": "success",
                        "last_completed_at": _now(),
                        "last_result_session_id": result.session_id,
                        "running": False,
                    },
                )
                self._trace_recorder.event(
                    "long_term_memory_extraction_completed",
                    {
                        "status": "success",
                        "memory_dir": self.store.memory_dir,
                        "child_session_id": result.session_id,
                    },
                )
            except Exception as exc:
                _merge_metadata(
                    state,
                    {
                        "last_status": "failed",
                        "last_completed_at": _now(),
                        "last_error_type": type(exc).__name__,
                        "running": False,
                    },
                )
                self._trace_recorder.event(
                    "long_term_memory_extraction_failed",
                    {"error_type": type(exc).__name__, "memory_dir": self.store.memory_dir},
                )


def should_extract_long_term_memory(
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
    *,
    tool_calls: tuple[Any, ...],
    enabled: bool = True,
) -> str:
    if not enabled:
        return "disabled"
    if tool_calls:
        return "tool_use_continuation"
    if state.metadata.get("query_source") == "compact":
        return "compact"
    if state.metadata.get("is_fork_child") is True:
        return "fork_child"
    if state.metadata.get("long_term_memory_extraction_agent") is True:
        return "extraction_child"
    if _main_agent_wrote_memory_this_turn(state):
        return "main_agent_memory_write"
    if _latest_cursor(messages) == _metadata(state).get("cursor"):
        return "cursor_current"
    return "extract"


def _extraction_prompt(
    store: LongTermMemoryStore,
    messages: tuple[dict[str, Any], ...],
    state: RuntimeState,
) -> str:
    cursor = _metadata(state).get("cursor") or "(none)"
    catalog = "\n".join(
        f"- {item.relative_path}: {item.description} ({item.type})"
        for item in store.scan()[:200]
    )
    return "\n".join(
        [
            "Update workspace-local OneCode long-term memory if the new conversation contains durable future-useful facts.",
            "",
            f"Memory directory: {store.memory_dir.resolve()}",
            f"Index file: {store.entrypoint_path.resolve()}",
            f"Last processed cursor: {cursor}",
            "",
            "Allowed memory types: user, feedback, project, reference.",
            "Rules:",
            "- Write only Markdown files under the memory directory.",
            "- Update existing topic files when possible; avoid duplicates.",
            "- Keep MEMORY.md as a concise index with one line per topic.",
            "- Do not save current task plans, short-term todos, secrets, or facts easily derived from repository files.",
            "- Do not investigate source files to verify the user. Base extraction on new conversation only.",
            "- If nothing should be saved, do not edit files and finish with a short statement.",
            "",
            "Existing memory catalog:",
            catalog or "(empty)",
        ]
    )


def _main_agent_wrote_memory_this_turn(state: RuntimeState) -> bool:
    writes = state.metadata.get("long_term_memory_writes", ())
    try:
        return any(
            isinstance(item, dict) and item.get("turn_count") == state.turn_count
            for item in writes
        )
    except TypeError:
        return False


def _latest_cursor(messages: tuple[dict[str, Any], ...]) -> str:
    if not messages:
        return ""
    message = messages[-1]
    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        for key in ("message_uuid", "uuid"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
    return f"message-{len(messages)}"


def _advance_cursor(messages: tuple[dict[str, Any], ...], state: RuntimeState) -> None:
    _merge_metadata(state, {"cursor": _latest_cursor(messages)})


def _metadata(state: RuntimeState) -> dict[str, Any]:
    value = state.metadata.get(LONG_TERM_MEMORY_EXTRACTION_KEY)
    if isinstance(value, dict):
        return value
    value = {}
    state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY] = value
    return value


def _merge_metadata(state: RuntimeState, updates: dict[str, Any]) -> None:
    value = dict(_metadata(state))
    value.update(updates)
    state.metadata[LONG_TERM_MEMORY_EXTRACTION_KEY] = value


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
