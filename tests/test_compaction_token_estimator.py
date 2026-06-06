from __future__ import annotations

from services.compaction.token_estimator import (
    DOCUMENT_BLOCK_TOKENS,
    IMAGE_BLOCK_TOKENS,
    UNKNOWN_BLOCK_TOKENS,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_snapshot_tokens,
)
from services.context.snapshot import ContextSnapshot


def test_estimates_string_message_with_conservative_char_ratio() -> None:
    message = {"role": "user", "content": "a" * 12}

    assert estimate_message_tokens(message) == 10


def test_estimates_tool_calls_and_metadata_json_fields() -> None:
    plain = {"role": "assistant", "content": ""}
    with_tool_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "name": "read_file"}],
        "metadata": {"stop_reason": "tool_calls"},
    }

    assert estimate_message_tokens(with_tool_call) > estimate_message_tokens(plain)


def test_estimates_multimodal_and_unknown_blocks() -> None:
    message = {
        "role": "user",
        "content": [
            {"type": "text", "text": "abcdef"},
            {"type": "image_url", "image_url": {"url": "file:///image.png"}},
            {"type": "document", "name": "spec.pdf"},
            {"type": "custom", "payload": "value"},
        ],
    }

    total = estimate_message_tokens(message)

    assert total >= IMAGE_BLOCK_TOKENS
    assert total >= DOCUMENT_BLOCK_TOKENS
    assert total >= UNKNOWN_BLOCK_TOKENS


def test_estimates_message_chain_and_snapshot() -> None:
    messages = (
        {"role": "user", "content": "hello"},
        {"role": "tool_result", "tool_call_id": "call-1", "content": "result"},
    )
    snapshot = ContextSnapshot(
        system_prompt="system",
        messages=messages,
        tool_schemas=({"type": "function", "function": {"name": "read_file"}},),
        usage_hints={"estimated": True},
        transcript_refs=("messages.jsonl",),
    )

    assert estimate_messages_tokens(messages) > 0
    assert estimate_snapshot_tokens(snapshot) > estimate_messages_tokens(messages)
