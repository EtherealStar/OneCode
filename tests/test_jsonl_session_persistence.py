from __future__ import annotations

import json
from pathlib import Path

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.transcript import JsonlTranscriptStore
from services.tools.types import ToolExecutionResult


def read_jsonl(path: Path) -> list[dict]:
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def make_store(
    tmp_path: Path,
    state: RuntimeState,
) -> MessageStore:
    return MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )


def test_message_store_persists_messages_with_parent_chain(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-parent")
    message_store = make_store(tmp_path, state)

    message_store.append_user("hello")
    message_store.append_assistant({"content": "hi"})
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call_read",
                tool_name="read_file",
                content="tool output",
            )
        ]
    )
    message_store.flush_transcript()

    messages_path = tmp_path / ".onecode" / state.session_id / "messages.jsonl"
    records = read_jsonl(messages_path)

    assert [record["type"] for record in records] == ["message", "message", "message"]
    assert [record["session_id"] for record in records] == [state.session_id] * 3
    assert records[0]["parent_uuid"] is None
    assert records[1]["parent_uuid"] == records[0]["uuid"]
    assert records[2]["parent_uuid"] == records[1]["uuid"]
    assert records[0]["message"] == {"role": "user", "content": "hello"}
    assert records[1]["message"] == {"content": "hi", "role": "assistant"}
    assert records[2]["message"]["content"] == "tool output"
    assert message_store.current_messages()[2]["content"] == "tool output"


def test_large_tool_result_is_externalized_and_restored(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-large")
    message_store = make_store(tmp_path, state)
    large_content = "x" * (50 * 1024 + 1)

    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call/read:1",
                tool_name="read_file",
                content=large_content,
            )
        ]
    )
    message_store.flush_transcript()

    session_dir = tmp_path / ".onecode" / state.session_id
    records = read_jsonl(session_dir / "messages.jsonl")
    record_message = records[0]["message"]
    metadata = record_message["metadata"]

    assert metadata["tool_result_externalized"] is True
    assert metadata["tool_result_path"] == "tool-results/call_read_1.txt"
    assert metadata["original_tool_call_id"] == "call/read:1"
    assert metadata["original_size_bytes"] == len(large_content.encode("utf-8"))
    assert record_message["content"] != large_content
    assert (session_dir / metadata["tool_result_path"]).read_text(
        encoding="utf-8"
    ) == large_content

    restored_state = RuntimeState()
    transcript_store = JsonlTranscriptStore(
        tmp_path / ".onecode",
        state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    restored_store = MessageStore.from_transcript(transcript_store, restored_state)

    assert restored_state.session_id == state.session_id
    assert restored_store.current_messages()[0]["content"] == large_content


def test_clear_starts_new_session_without_deleting_old_transcript(
    tmp_path: Path,
) -> None:
    state = RuntimeState(session_id="session-old")
    message_store = make_store(tmp_path, state)
    message_store.append_user("old message")
    message_store.flush_transcript()
    old_messages_path = tmp_path / ".onecode" / "session-old" / "messages.jsonl"

    new_session_id = state.start_new_session()
    message_store.clear_for_new_session(new_session_id)
    message_store.append_user("new message")
    message_store.flush_transcript()

    new_messages_path = tmp_path / ".onecode" / new_session_id / "messages.jsonl"
    assert message_store.current_messages() == (
        {"role": "user", "content": "new message"},
    )
    assert old_messages_path.exists()
    assert read_jsonl(old_messages_path)[0]["message"]["content"] == "old message"
    assert read_jsonl(new_messages_path)[0]["message"]["content"] == "new message"


def test_replace_messages_for_compaction_appends_new_chain_without_deleting_history(
    tmp_path: Path,
) -> None:
    state = RuntimeState(session_id="session-compact")
    message_store = make_store(tmp_path, state)
    message_store.append_user("old question")
    message_store.append_assistant({"content": "old answer"})

    stored = message_store.replace_messages_for_compaction(
        (
            {
                "role": "user",
                "content": "[Compact boundary]",
                "metadata": {"is_compact_boundary": True},
            },
            {"role": "user", "content": "Summary: old work"},
        ),
        reason="manual",
        metadata={"boundary_id": "boundary-1"},
    )
    message_store.flush_transcript()

    messages_path = tmp_path / ".onecode" / state.session_id / "messages.jsonl"
    records = read_jsonl(messages_path)

    assert message_store.current_messages() == tuple(stored)
    assert [record["message"]["content"] for record in records] == [
        "old question",
        "old answer",
        "[Compact boundary]",
        "Summary: old work",
    ]
    assert records[2]["parent_uuid"] == records[1]["uuid"]
    assert records[2]["message"]["metadata"]["compaction"]["reason"] == "manual"
    assert records[2]["message"]["metadata"]["compaction"]["boundary_id"] == "boundary-1"


def test_restore_skips_bad_lines_and_continues_same_session(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-restore")
    message_store = make_store(tmp_path, state)
    message_store.append_user("first")
    message_store.flush_transcript()

    messages_path = tmp_path / ".onecode" / state.session_id / "messages.jsonl"
    with messages_path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
        handle.write('{"type":"message","message":{"role":"unknown"}}\n')

    restored_state = RuntimeState()
    transcript_store = JsonlTranscriptStore(
        tmp_path / ".onecode",
        state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    restored_store = MessageStore.from_transcript(transcript_store, restored_state)
    restored_store.append_assistant({"content": "second"})
    restored_store.flush_transcript()

    records = read_jsonl(messages_path)
    assert restored_state.session_id == state.session_id
    assert restored_store.current_messages() == (
        {"role": "user", "content": "first"},
        {"content": "second", "role": "assistant"},
    )
    assert records[-1]["message"]["content"] == "second"
