from __future__ import annotations

from onecode.compaction import CompactionConfig, Compactor


def test_tool_result_budget_persists_large_result(tmp_path):
    compactor = Compactor(
        CompactionConfig(
            state_dir=tmp_path / ".onecode",
            tool_result_total_budget_chars=100,
            tool_result_persist_threshold_chars=50,
            tool_result_preview_chars=10,
        )
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "abc", "content": "x" * 200}
            ],
        }
    ]

    compacted = compactor.apply_tool_result_budget(messages)

    content = compacted[0]["content"][0]["content"]
    assert "<persisted-output" in content
    assert (tmp_path / ".onecode" / "tool-results" / "abc.txt").exists()


def test_cleanup_old_tool_results_keeps_recent(tmp_path):
    compactor = Compactor(
        CompactionConfig(state_dir=tmp_path / ".onecode", keep_recent_tool_results=1)
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "old", "content": "o" * 200}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "new", "content": "n" * 200}
            ],
        },
    ]

    compacted = compactor.cleanup_old_tool_results(messages)

    assert compacted[0]["content"][0]["content"].startswith("[Earlier tool result compacted")
    assert compacted[1]["content"][0]["content"] == "n" * 200
