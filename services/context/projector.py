"""Project internal messages into a model-visible safe window."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class ContextProjector:
    def __init__(
        self,
        *,
        start_index: int = 0,
        max_messages: int | None = None,
    ) -> None:
        self.start_index = max(0, start_index)
        self.max_messages = max_messages if max_messages is None else max(0, max_messages)

    def project(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        """Return a deep-copied projection that keeps tool call pairs intact."""

        if not messages:
            return ()
        start_index = self.start_index
        if self.max_messages is not None and len(messages) > self.max_messages:
            start_index = max(start_index, len(messages) - self.max_messages)
        start_index = self.adjust_start_index_to_preserve_tool_pairs(
            messages,
            start_index,
        )
        projected = messages[start_index:]
        return tuple(deepcopy(message) for message in _drop_unpaired_tool_results(projected))

    def adjust_start_index_to_preserve_tool_pairs(
        self,
        messages: tuple[dict[str, Any], ...],
        start_index: int,
    ) -> int:
        """Move a cut point backward when retained tool results need their calls."""

        if start_index <= 0:
            return 0
        adjusted = min(start_index, len(messages))
        assistant_indexes = _assistant_call_indexes(messages)
        changed = True
        while changed:
            changed = False
            retained_tool_result_ids = _tool_result_ids(messages[adjusted:])
            for tool_call_id in retained_tool_result_ids:
                assistant_index = assistant_indexes.get(tool_call_id)
                if assistant_index is not None and assistant_index < adjusted:
                    adjusted = assistant_index
                    changed = True
                    break
        return adjusted


def _assistant_call_indexes(messages: tuple[dict[str, Any], ...]) -> dict[str, int]:
    indexes: dict[str, int] = {}
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        for tool_call_id in _assistant_tool_call_ids(message):
            indexes.setdefault(tool_call_id, index)
    return indexes


def _assistant_tool_call_ids(message: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if isinstance(call_id, str) and call_id:
                ids.append(call_id)
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call_id = block.get("id")
            if isinstance(call_id, str) and call_id:
                ids.append(call_id)
    return tuple(ids)


def _tool_result_ids(messages: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    ids: list[str] = []
    for message in messages:
        if message.get("role") != "tool_result":
            continue
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            ids.append(tool_call_id)
    return tuple(ids)


def _drop_unpaired_tool_results(
    messages: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    visible_call_ids: set[str] = set()
    for message in messages:
        if message.get("role") == "assistant":
            visible_call_ids.update(_assistant_tool_call_ids(message))

    kept: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "tool_result":
            kept.append(message)
            continue
        tool_call_id = message.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id in visible_call_ids:
            kept.append(message)
    return tuple(kept)
