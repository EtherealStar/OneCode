from __future__ import annotations

import json
from pathlib import Path

from application.history import load_conversation_history
from core.runtime_state import RuntimeState
from services.attachments.types import AttachmentMessage
from services.context.message_store import MessageStore
from services.context.transcript import JsonlTranscriptStore
from services.tools.types import ToolExecutionResult


def make_store(tmp_path: Path, session_id: str = "session-history") -> MessageStore:
    return MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )


def read_records(store: MessageStore) -> list[dict]:
    path = store.transcript_store.messages_path
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def history_texts(store: MessageStore) -> list[str]:
    return [record.text for record in load_conversation_history(store.transcript_store).records]


def test_history_preserves_repeated_identical_user_text(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("same question")
    store.append_assistant({"content": "first answer"})
    store.append_user("same question")
    store.append_assistant({"content": "second answer"})
    store.flush_transcript()

    history = load_conversation_history(store.transcript_store)

    user_records = [record for record in history.records if record.role == "user"]
    assert [record.text for record in user_records] == ["same question", "same question"]
    assert len({record.uuid for record in user_records}) == 2


def test_history_keeps_pre_compaction_messages_without_copy_duplicates(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.append_user("q1")
    store.append_assistant({"content": "a1"})
    store.append_user("q2")
    store.append_assistant({"content": "a2"})
    records = store.active_records()
    q2, a2 = records[-2], records[-1]

    store.replace_messages_for_compaction(
        [
            {
                "role": "user",
                "content": "[Compact boundary]",
                "metadata": {"is_compact_boundary": True},
            },
            {
                "role": "user",
                "content": "Summary of q1/a1",
                "metadata": {"is_compact_summary": True},
            },
            q2.message,
            a2.message,
        ],
        reason="manual",
        metadata={"boundary_id": "b1"},
        source_uuids=[None, None, q2.uuid, a2.uuid],
    )
    store.append_user("q3")
    store.append_assistant({"content": "a3"})
    store.flush_transcript()

    texts = history_texts(store)

    assert texts == ["q1", "a1", "q2", "a2", "q3", "a3"]
    assert "[Compact boundary]" not in texts
    assert "Summary of q1/a1" not in texts


def test_history_survives_two_compactions(tmp_path: Path) -> None:
    store = make_store(tmp_path, "session-two-compact")
    store.append_user("q1")
    store.append_assistant({"content": "a1"})
    store.append_user("q2")
    store.append_assistant({"content": "a2"})

    first = store.active_records()
    store.replace_messages_for_compaction(
        [
            {
                "role": "user",
                "content": "[Compact boundary 1]",
                "metadata": {"is_compact_boundary": True},
            },
            {
                "role": "user",
                "content": "Summary 1",
                "metadata": {"is_compact_summary": True},
            },
            first[-1].message,
        ],
        reason="manual",
        source_uuids=[None, None, first[-1].uuid],
    )
    store.append_user("q3")
    store.append_assistant({"content": "a3"})

    second = store.active_records()
    store.replace_messages_for_compaction(
        [
            {
                "role": "user",
                "content": "[Compact boundary 2]",
                "metadata": {"is_compact_boundary": True},
            },
            {
                "role": "user",
                "content": "Summary 2",
                "metadata": {"is_compact_summary": True},
            },
            second[-2].message,
            second[-1].message,
        ],
        reason="manual",
        source_uuids=[None, None, second[-2].uuid, second[-1].uuid],
    )
    store.flush_transcript()

    texts = history_texts(store)

    assert texts == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_model_active_chain_and_history_are_separate(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-history-chain")
    store = make_store(tmp_path, state.session_id)
    store.append_user("old question")
    store.append_assistant({"content": "old answer"})
    records = store.active_records()
    store.replace_messages_for_compaction(
        [
            {
                "role": "user",
                "content": "[Compact boundary]",
                "metadata": {"is_compact_boundary": True},
            },
            {
                "role": "user",
                "content": "Summary",
                "metadata": {"is_compact_summary": True},
            },
            records[-1].message,
        ],
        reason="manual",
        metadata={"boundary_id": "b1"},
        source_uuids=[None, None, records[-1].uuid],
    )
    store.append_user("new question")
    store.flush_transcript()

    restored = MessageStore.from_transcript(
        JsonlTranscriptStore(
            tmp_path / ".onecode",
            state.session_id,
            cwd=tmp_path,
            flush_interval_seconds=60,
        ),
        state,
    )
    active_texts = [
        message.get("content") for message in restored.current_messages()
    ]
    history_texts_value = [
        record.text for record in load_conversation_history(restored.transcript_store).records
    ]

    assert active_texts == ["[Compact boundary]", "Summary", "old answer", "new question"]
    assert history_texts_value == ["old question", "old answer", "new question"]

    disk = read_records(store)
    copies = [record for record in disk if record.get("source_uuid")]
    assert copies and all(record.get("record_kind") == "compaction" for record in copies)


def test_history_skips_legacy_compaction_copies_without_source(
    tmp_path: Path,
) -> None:
    store = JsonlTranscriptStore(
        tmp_path / ".onecode",
        "session-legacy-compact",
        cwd=tmp_path,
        flush_interval_seconds=60,
    )

    def add(record_uuid: str, parent: str | None, message: dict) -> None:
        store.append_message(message, message_uuid=record_uuid, parent_uuid=parent)

    add("u1", None, {"role": "user", "content": "q1"})
    add("a1", "u1", {"role": "assistant", "content": "a1"})
    add("u2", "a1", {"role": "user", "content": "q2"})
    add("a2", "u2", {"role": "assistant", "content": "a2"})
    compaction = {"reason": "manual", "boundary_id": "b1"}
    add(
        "b1",
        None,
        {
            "role": "user",
            "content": "[Compact boundary]",
            "metadata": {"is_compact_boundary": True, "compaction": compaction},
        },
    )
    add(
        "s1",
        "b1",
        {
            "role": "user",
            "content": "Summary",
            "metadata": {"is_compact_summary": True, "compaction": compaction},
        },
    )
    add("c2", "s1", {"role": "user", "content": "q2", "metadata": {"compaction": compaction}})
    add(
        "ca2",
        "c2",
        {"role": "assistant", "content": "a2", "metadata": {"compaction": compaction}},
    )
    add("u3", "ca2", {"role": "user", "content": "q3"})
    add("a3", "u3", {"role": "assistant", "content": "a3"})
    store.flush()

    history = load_conversation_history(store)

    assert [record.text for record in history.records] == [
        "q1",
        "a1",
        "q2",
        "a2",
        "q3",
        "a3",
    ]


def test_history_associates_attachment_summary_with_user(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("please read @note.txt")
    store.append_attachments(
        [
            AttachmentMessage(
                attachment={"type": "file", "path": "note.txt", "content": "hello"}
            ).to_message()
        ]
    )
    store.append_assistant({"content": "done"})
    store.flush_transcript()

    history = load_conversation_history(store.transcript_store)

    assert [record.role for record in history.records] == ["user", "assistant"]
    user = history.records[0]
    assert len(user.attachments) == 1
    assert user.attachments[0].attachment_type == "file"
    assert "note.txt" in user.attachments[0].summary


def test_history_preserves_real_tool_failure(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.append_user("inspect")
    store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "read_file"}}],
        }
    )
    store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-1",
                tool_name="read_file",
                content='{"error": "file_not_found"}',
                is_error=True,
            )
        ]
    )
    store.flush_transcript()

    history = load_conversation_history(store.transcript_store)

    assistant = next(record for record in history.records if record.role == "assistant")
    tool_result = next(record for record in history.records if record.role == "tool_result")
    assert assistant.tool_calls[0].tool_call_id == "call-1"
    assert tool_result.tool_call_id == "call-1"
    assert tool_result.is_error is True


def test_history_does_not_cross_link_duplicate_tool_ids(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for turn in range(2):
        store.append_user(f"inspect {turn}")
        store.append_assistant(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-dup", "function": {"name": "read_file"}}],
            }
        )
        store.append_tool_results(
            [
                ToolExecutionResult(
                    tool_call_id="call-dup",
                    tool_name="read_file",
                    content=f"result {turn}",
                )
            ]
        )
    store.flush_transcript()

    history = load_conversation_history(store.transcript_store)

    results = [record for record in history.records if record.role == "tool_result"]
    assert [record.text for record in results] == ["result 0", "result 1"]
    assert len({record.uuid for record in results}) == 2


def test_history_survives_missing_external_result(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    content = "y" * (50 * 1024 + 10)
    store.append_user("big output")
    store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-missing",
                tool_name="grep",
                content=content,
            )
        ]
    )
    store.flush_transcript()

    records = read_records(store)
    relative = records[-1]["message"]["metadata"]["tool_result_path"]
    (store.transcript_store.session_dir / relative).unlink()

    history = load_conversation_history(store.transcript_store)

    result = next(record for record in history.records if record.role == "tool_result")
    assert result.externalized is True
    assert result.text
