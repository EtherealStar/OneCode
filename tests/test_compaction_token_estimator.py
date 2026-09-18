from __future__ import annotations

from services.compaction.token_estimator import estimate_message_tokens


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
