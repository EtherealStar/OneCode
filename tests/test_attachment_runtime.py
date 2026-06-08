from __future__ import annotations

import asyncio

from core.context_engine import ContextEngine, StaticPromptAssembler
from core.runtime_state import RuntimeState
from services.attachments.context_preparer import AttachmentContextPreparer
from services.attachments.types import AttachmentMessage
from services.context.message_store import MessageStore
from services.context.transcript import JsonlTranscriptStore


def test_context_engine_projects_attachments_before_provider() -> None:
    state = RuntimeState()
    store = MessageStore(session_id=state.session_id)
    store.append_user("summarize @note.txt")
    store.append_attachments(
        [
            AttachmentMessage(
                attachment={
                    "type": "file",
                    "path": "note.txt",
                    "content": "1\tone",
                    "offset": 1,
                    "limit": 1,
                },
                attachment_id="att_runtime",
                source="user_input",
            ).to_message()
        ]
    )
    engine = ContextEngine(
        store,
        prompt_assembler=StaticPromptAssembler("system"),
        context_preparer=AttachmentContextPreparer(),
    )

    snapshot = asyncio.run(engine.build_for_model(state))

    assert all(message["role"] != "attachment" for message in snapshot.messages)
    assert any(message["role"] == "tool_result" for message in snapshot.messages)
    assert store.current_messages()[-1]["role"] == "attachment"


def test_transcript_restores_attachment_messages(tmp_path) -> None:
    state = RuntimeState()
    store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    attachment = AttachmentMessage(
        attachment={"type": "attachment_error", "error": "not_found"},
        attachment_id="att_restore",
        source="user_input",
    ).to_message()
    store.append_attachments([attachment])
    store.flush_transcript()

    restored = MessageStore.from_transcript(
        JsonlTranscriptStore(tmp_path / ".onecode", state.session_id, cwd=tmp_path),
        state,
    )

    assert restored.current_messages() == (attachment,)
