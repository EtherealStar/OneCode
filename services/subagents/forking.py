"""Message construction for fork subagents."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

FORK_PLACEHOLDER_RESULT = "Fork started - processing in child agent"
FORK_DIRECTIVE_TEMPLATE = """You are a fork worker, not the main agent.

You inherited the parent agent's message history for context. Do not call the
agent tool or spawn another subagent. Use the available tools directly to finish
only the directive below. Return a concise, factual answer scoped to this
directive.

Directive:
{directive}
"""


def build_forked_messages(
    parent_messages: Iterable[dict[str, Any]],
    directive: str,
) -> tuple[dict[str, Any], ...]:
    """Deep-copy parent history and append fork repair messages plus directive."""

    messages = [deepcopy(message) for message in parent_messages]
    existing_results = {
        str(message.get("tool_call_id"))
        for message in messages
        if message.get("role") == "tool_result" and message.get("tool_call_id")
    }
    if messages and messages[-1].get("role") == "assistant":
        for tool_call in _assistant_tool_calls(messages[-1]):
            tool_call_id = str(tool_call.get("id", ""))
            if not tool_call_id or tool_call_id in existing_results:
                continue
            messages.append(
                {
                    "role": "tool_result",
                    "tool_call_id": tool_call_id,
                    "tool_name": str(tool_call.get("name", "")),
                    "content": FORK_PLACEHOLDER_RESULT,
                    "is_error": False,
                    "metadata": {"placeholder": "fork_started"},
                }
            )
            existing_results.add(tool_call_id)
    messages.append(
        {
            "role": "user",
            "content": FORK_DIRECTIVE_TEMPLATE.format(directive=directive),
        }
    )
    return tuple(messages)


def _assistant_tool_calls(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    content = message.get("content")
    calls: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            calls.append(
                {
                    "id": block.get("id"),
                    "name": block.get("name"),
                }
            )
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            calls.append({"id": call.get("id"), "name": call.get("name")})
    return tuple(calls)
