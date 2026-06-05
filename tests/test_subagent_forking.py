from __future__ import annotations

from services.subagents.forking import FORK_PLACEHOLDER_RESULT, build_forked_messages


def test_build_forked_messages_deep_copies_parent_and_repairs_tool_prefix() -> None:
    parent = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-agent",
                    "name": "agent",
                    "input": {"prompt": "where is parser"},
                }
            ],
        },
    ]

    forked = build_forked_messages(parent, "finish the delegated search")

    assert parent[-1]["content"][0]["input"]["prompt"] == "where is parser"
    assert forked[0] == parent[0]
    assert forked[1] == parent[1]
    assert forked[1] is not parent[1]
    assert forked[1]["content"] is not parent[1]["content"]
    assert forked[2] == {
        "role": "tool_result",
        "tool_call_id": "call-agent",
        "tool_name": "agent",
        "content": FORK_PLACEHOLDER_RESULT,
        "is_error": False,
        "metadata": {"placeholder": "fork_started"},
    }
    assert forked[3]["role"] == "user"
    assert "fork worker" in forked[3]["content"]
    assert "finish the delegated search" in forked[3]["content"]


def test_build_forked_messages_does_not_duplicate_existing_tool_result() -> None:
    parent = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": "agent"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "agent",
            "content": "already present",
        },
    ]

    forked = build_forked_messages(parent, "continue")

    assert [message["role"] for message in forked] == [
        "assistant",
        "tool_result",
        "user",
    ]
