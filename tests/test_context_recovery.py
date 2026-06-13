from __future__ import annotations

import json
from pathlib import Path

from core.runtime_state import RuntimeState
from services.context.message_store import MessageStore
from services.context.recovery import restore_transcript_active_chain
from services.context.transcript import JsonlTranscriptStore
from services.tools.types import ToolExecutionResult


def make_store(tmp_path: Path, session_id: str = "session-recovery") -> MessageStore:
    return MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )


def test_restore_drops_orphan_tool_result(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-orphan",
                tool_name="read_file",
                content="orphan",
            )
        ]
    )
    store.flush_transcript()

    restored = restore_transcript_active_chain(store.transcript_store)

    assert restored.messages == ()
    assert restored.last_uuid is None
    assert restored.warnings == ("dropped_orphan_tool_result:" + _record_uuid(store),)


def test_restore_inserts_synthetic_result_for_interrupted_tool_call(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_assistant(
        {
            "content": "",
            "tool_calls": [
                {"id": "call-read", "function": {"name": "read_file"}},
            ],
        }
    )
    store.flush_transcript()

    restored = restore_transcript_active_chain(store.transcript_store)

    assert len(restored.messages) == 2
    assert restored.messages[0]["role"] == "assistant"
    assert restored.messages[1]["role"] == "tool_result"
    assert restored.messages[1]["tool_call_id"] == "call-read"
    assert restored.messages[1]["tool_name"] == "read_file"
    assert restored.messages[1]["is_error"] is True
    assert restored.messages[1]["metadata"]["synthetic"] is True


def test_restore_filters_blank_assistant_without_tool_calls(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("hello")
    store.append_assistant({"content": "   "})
    store.flush_transcript()

    restored_state = RuntimeState()
    restored_store = MessageStore.from_transcript(
        JsonlTranscriptStore(
            tmp_path / ".onecode",
            store.session_id,
            cwd=tmp_path,
            flush_interval_seconds=60,
        ),
        restored_state,
    )

    assert restored_store.current_messages() == ({"role": "user", "content": "hello"},)


def test_restore_uses_latest_leaf_active_chain(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("old question")
    store.append_assistant({"content": "old answer"})
    store.replace_messages_for_compaction(
        (
            {
                "role": "user",
                "content": "[Compact boundary]",
                "metadata": {"is_compact_boundary": True},
            },
            {"role": "user", "content": "Summary: old work"},
        ),
        reason="manual",
    )
    store.append_user("after compact")
    store.flush_transcript()

    restored = restore_transcript_active_chain(store.transcript_store)

    assert [message["content"] for message in restored.messages] == [
        "[Compact boundary]",
        "Summary: old work",
        "after compact",
    ]


def _record_uuid(store: MessageStore) -> str:
    path = store.transcript_store.messages_path
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    return record["uuid"]
