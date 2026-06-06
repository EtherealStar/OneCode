from __future__ import annotations

from services.context.projector import ContextProjector


def test_projector_moves_cut_back_to_keep_tool_call_for_result() -> None:
    messages = (
        {"role": "user", "content": "question"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "read_file",
            "content": "file content",
        },
    )

    projector = ContextProjector()

    assert projector.adjust_start_index_to_preserve_tool_pairs(messages, 2) == 1


def test_projector_max_messages_preserves_assistant_tool_pair() -> None:
    messages = (
        {"role": "user", "content": "old"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "read_file",
            "content": "file content",
        },
    )

    projected = ContextProjector(max_messages=1).project(messages)

    assert projected == messages[1:]


def test_projector_drops_tool_result_when_assistant_call_is_unavailable() -> None:
    messages = (
        {"role": "user", "content": "recent"},
        {
            "role": "tool_result",
            "tool_call_id": "missing-call",
            "tool_name": "read_file",
            "content": "orphan",
        },
    )

    projected = ContextProjector().project(messages)

    assert projected == ({"role": "user", "content": "recent"},)


def test_projector_returns_deep_copies() -> None:
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "read_file"}],
        },
    )

    projected = ContextProjector().project(messages)
    projected[0]["tool_calls"][0]["name"] = "changed"

    assert messages[0]["tool_calls"][0]["name"] == "read_file"
