from __future__ import annotations

import asyncio

from core.context_engine import ContextEngine, StaticPromptAssembler
from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.catalog import get_provider_definition
from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
)
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


def test_openai_payload_receives_projected_attachment_context() -> None:
    state = RuntimeState()
    store = MessageStore(session_id=state.session_id)
    store.append_attachments(
        [
            AttachmentMessage(
                attachment={
                    "type": "file",
                    "path": "note.txt",
                    "content": "1\tone",
                },
                attachment_id="att_payload",
            ).to_message()
        ]
    )
    engine = ContextEngine(
        store,
        prompt_assembler=StaticPromptAssembler(),
        context_preparer=AttachmentContextPreparer(),
    )
    snapshot = asyncio.run(engine.build_for_model(state))
    client = OpenAICompatibleChatCompletionsClient(_resolved_config())

    payload = client._build_payload(snapshot)

    roles = [message["role"] for message in payload["messages"]]
    assert "attachment" not in roles
    assert roles == ["assistant", "tool"]


def _resolved_config() -> ResolvedProviderConfig:
    provider = get_provider_definition("openai")
    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        "https://api.openai.com/v1",
        "gpt-test",
        "secret",
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )
