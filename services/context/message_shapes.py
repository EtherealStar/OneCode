"""工具调用消息形态的共享中立辅助函数。

OneCode 在内部以两种形式存储 assistant 工具声明：顶层 tool_calls 列表，
以及 type == "tool_use" 的 content 块。清理和恢复逻辑必须以相同方式处理这两种形式。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def assistant_tool_call_ids(message: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """针对两种声明表示形式，均返回 (call_id, tool_name)。"""

    return tuple(
        (call_id, name)
        for call_id, name, _input in assistant_tool_declarations(message)
    )


def assistant_tool_declarations(
    message: dict[str, Any],
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    """针对两种声明形式，均返回 (call_id, tool_name, input)。

    工具输入从存储的中立形态中解析，以便历史浏览能够展示与实时流相同的参数摘要，
    而无需读取文件或重新解析用户文本。
    """

    declarations: list[tuple[str, str, dict[str, Any]]] = []
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
            declarations.append(
                (
                    call_id,
                    name if isinstance(name, str) else "unknown_tool",
                    _declared_input(call),
                )
            )

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call_id = block.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            name = block.get("name")
            declarations.append(
                (
                    call_id,
                    name if isinstance(name, str) else "unknown_tool",
                    _declared_input(block),
                )
            )
    return tuple(declarations)


def _declared_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("input")
    if isinstance(value, Mapping):
        return dict(value)
    for candidate in (payload.get("arguments"), _function_arguments(payload)):
        parsed = _parse_arguments(candidate)
        if parsed is not None:
            return parsed
    return {}


def _function_arguments(payload: Mapping[str, Any]) -> Any:
    function = payload.get("function")
    if isinstance(function, Mapping):
        return function.get("arguments")
    return None


def _parse_arguments(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


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
    """丢弃调用 ID 不在 keep_ids 中的声明。"""

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
