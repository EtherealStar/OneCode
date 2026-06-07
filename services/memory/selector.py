"""LLM side-query selector for relevant long-term memories."""

from __future__ import annotations

import json
from typing import Any

from core.runtime_state import RuntimeState
from services.context.snapshot import ContextSnapshot
from services.memory.types import LongTermMemoryFile
from services.model.client import ModelClient
from services.observability import TraceRecorder

SELECTOR_SYSTEM_PROMPT = (
    "Select at most five long-term memory files relevant to the current turn. "
    "Return only JSON: {\"selected_memories\": [\"relative/path.md\"]}. "
    "Use only filenames present in the catalog."
)


class RelevantMemorySelector:
    def __init__(
        self,
        model_client: ModelClient,
        *,
        trace_recorder: TraceRecorder | None = None,
        max_items: int = 5,
    ) -> None:
        self._model_client = model_client
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._max_items = max(1, max_items)

    async def select(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        catalog: tuple[LongTermMemoryFile, ...],
    ) -> tuple[LongTermMemoryFile, ...]:
        if not catalog:
            return ()
        allowed = {item.relative_path: item for item in catalog}
        try:
            final_text = ""
            snapshot = ContextSnapshot(
                system_prompt=SELECTOR_SYSTEM_PROMPT,
                messages=(
                    {
                        "role": "user",
                        "content": _selector_payload(messages, catalog),
                    },
                ),
                tool_schemas=(),
                transition=state.last_transition.value if state.last_transition else None,
            )
            async for event in self._model_client.stream(snapshot):
                if event.type == "message_completed":
                    final_text = event.final_text or _message_text(event.assistant_message)
            selected = self._parse_selection(final_text, allowed)
        except Exception as exc:
            self._trace_recorder.event(
                "long_term_memory_selector_failed",
                {"error_type": type(exc).__name__},
            )
            return ()
        self._trace_recorder.event(
            "long_term_memory_selector_completed",
            {"selected_count": len(selected), "catalog_count": len(catalog)},
        )
        return selected

    def _parse_selection(
        self,
        text: str,
        allowed: dict[str, LongTermMemoryFile],
    ) -> tuple[LongTermMemoryFile, ...]:
        try:
            parsed = json.loads(text.strip())
        except json.JSONDecodeError:
            return ()
        if not isinstance(parsed, dict):
            return ()
        raw = parsed.get("selected_memories")
        if not isinstance(raw, list):
            return ()
        selected: list[LongTermMemoryFile] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            memory = allowed.get(item)
            if memory is None or memory in selected:
                continue
            selected.append(memory)
            if len(selected) >= self._max_items:
                break
        return tuple(selected)


def _selector_payload(
    messages: tuple[dict[str, Any], ...],
    catalog: tuple[LongTermMemoryFile, ...],
) -> str:
    recent = messages[-8:]
    conversation = "\n".join(
        f"{message.get('role', 'message')}: {_preview(_message_text(message), 500)}"
        for message in recent
    )
    catalog_lines = "\n".join(
        (
            f"- {item.relative_path} | name={item.name} | type={item.type} | "
            f"description={item.description} | mtime={item.mtime}"
        )
        for item in catalog[:200]
    )
    return f"Recent conversation:\n{conversation}\n\nMemory catalog:\n{catalog_lines}"


def _message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
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


def _preview(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 3].rstrip() + "..."
