"""Shared, provider-neutral helpers for tool-call message shapes.

OneCode stores assistant tool declarations in two internal forms: the
top-level ``tool_calls`` list and ``content`` blocks with ``type ==
"tool_use"``. Cleanup and recovery must treat both the same way.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def assistant_tool_call_ids(message: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Return ``(call_id, tool_name)`` for both declaration representations."""

    ids: list[tuple[str, str]] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            name = call.get("name")
            function = call.get("function")
            if not isinstance(name, str) and isinstance(function, dict):
                function_name = function.get("name")
                if isinstance(function_name, str):
                    name = function_name
            ids.append((call_id, name if isinstance(name, str) else "unknown_tool"))

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call_id = block.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            name = block.get("name")
            ids.append((call_id, name if isinstance(name, str) else "unknown_tool"))
    return tuple(ids)


def assistant_declarations(message: dict[str, Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for call_id, _name in assistant_tool_call_ids(message):
        if call_id not in seen:
            seen.append(call_id)
    return tuple(seen)


def prune_assistant_declarations(
    message: dict[str, Any],
    keep_ids: frozenset[str] | set[str],
) -> dict[str, Any]:
    """Drop declarations whose call IDs are not in ``keep_ids``."""

    pruned = deepcopy(message)
    raw_calls = pruned.get("tool_calls")
    if isinstance(raw_calls, list):
        kept_calls = [
            call
            for call in raw_calls
            if isinstance(call, dict) and call.get("id") in keep_ids
        ]
        if kept_calls:
            pruned["tool_calls"] = kept_calls
        else:
            pruned.pop("tool_calls", None)

    content = pruned.get("content")
    if isinstance(content, list):
        kept_blocks: list[Any] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if block.get("id") in keep_ids:
                    kept_blocks.append(block)
                continue
            kept_blocks.append(block)
        pruned["content"] = kept_blocks
    return pruned


def message_has_body(message: dict[str, Any]) -> bool:
    if message.get("tool_calls"):
        return True
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True
            if isinstance(block, str) and block.strip():
                return True
            if isinstance(block, dict) and str(block.get("text", "")).strip():
                return True
        return False
    return content is not None
