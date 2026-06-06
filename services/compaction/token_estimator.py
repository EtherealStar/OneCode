"""Conservative local token estimation for compaction decisions."""

from __future__ import annotations

from math import ceil
from typing import Any
import json

from services.context.snapshot import ContextSnapshot

MESSAGE_OVERHEAD_TOKENS = 4
IMAGE_BLOCK_TOKENS = 1_024
DOCUMENT_BLOCK_TOKENS = 2_048
UNKNOWN_BLOCK_TOKENS = 256


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate tokens for one provider-neutral internal message."""

    total = MESSAGE_OVERHEAD_TOKENS
    total += _estimate_content_tokens(message.get("content"))
    total += _estimate_json_field_tokens(message.get("tool_calls"))
    total += _estimate_json_field_tokens(message.get("metadata"))
    for field_name in ("role", "tool_call_id", "tool_name"):
        value = message.get(field_name)
        if isinstance(value, str):
            total += _estimate_text_tokens(value)
    return max(1, total)


def estimate_messages_tokens(messages: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> int:
    """Estimate tokens for an ordered message chain."""

    return sum(estimate_message_tokens(message) for message in messages)


def estimate_snapshot_tokens(snapshot: ContextSnapshot) -> int:
    """Estimate all model-visible input tokens in a context snapshot."""

    return (
        _estimate_text_tokens(snapshot.system_prompt)
        + estimate_messages_tokens(snapshot.messages)
        + _estimate_json_field_tokens(snapshot.tool_schemas)
        + _estimate_json_field_tokens(snapshot.usage_hints)
        + _estimate_json_field_tokens(snapshot.transcript_refs)
    )


def _estimate_content_tokens(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return _estimate_text_tokens(content)
    if isinstance(content, list):
        return sum(_estimate_block_tokens(block) for block in content)
    return _estimate_json_field_tokens(content)


def _estimate_block_tokens(block: Any) -> int:
    if isinstance(block, str):
        return _estimate_text_tokens(block)
    if not isinstance(block, dict):
        return UNKNOWN_BLOCK_TOKENS + _estimate_json_field_tokens(block)

    block_type = block.get("type")
    if block_type in {"text", "input_text"}:
        text = block.get("text")
        return _estimate_text_tokens(text if isinstance(text, str) else "")
    if block_type in {"image", "image_url", "input_image"}:
        return IMAGE_BLOCK_TOKENS
    if block_type in {"document", "file", "input_file"}:
        return DOCUMENT_BLOCK_TOKENS
    return UNKNOWN_BLOCK_TOKENS + _estimate_json_field_tokens(block)


def _estimate_json_field_tokens(value: Any) -> int:
    if value in (None, "", (), [], {}):
        return 0
    try:
        rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        rendered = repr(value)
    return _estimate_text_tokens(rendered)


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    # Character count divided by 4, multiplied by a 4/3 safety factor.
    return ceil(len(text) / 3)
