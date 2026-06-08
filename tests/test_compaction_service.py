from __future__ import annotations

import asyncio

from core.runtime_state import RuntimeState
from core.context_engine import ContextEngine
from services.compaction import ContextCompactionService
from utils.toolResultStorage import ToolResultStorage
from services.compaction.service import MICROCOMPACT_PLACEHOLDER
from services.compaction.types import CompactionConfig, CompactionTrigger
from services.context.message_store import MessageStore
from services.tools.types import ToolExecutionResult


def _prepare(
    service: ContextCompactionService,
    messages: tuple[dict, ...],
    state: RuntimeState,
):
    return asyncio.run(service.prepare_for_model(messages, state))


def test_prepare_for_model_persists_large_tool_results_before_projection(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    store = ToolResultStorage(tmp_path / ".onecode" / state.session_id)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "grep",
            "content": "abcdef",
        },
    )

    result = _prepare(service, messages, state)

    projected_result = result.messages[1]
    assert result.trigger == CompactionTrigger.MICRO
    assert projected_result["metadata"]["result_stored"] is True
    assert "Preview:\nab" in projected_result["content"]
    assert result.transcript_refs == (projected_result["metadata"]["stored_result_path"],)
    assert (store.results_dir / "call-1.txt").read_text(encoding="utf-8") == "abcdef"
    assert messages[1]["content"] == "abcdef"
    assert state.metadata["last_compaction"]["trigger"] == "micro"


def test_prepare_for_model_reuses_stored_large_tool_result_reference(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    store = ToolResultStorage(tmp_path / ".onecode" / state.session_id)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call-1",
            "tool_name": "grep",
            "content": "abcdef",
        },
    )

    first = _prepare(service, messages, state)
    second = _prepare(service, messages, state)

    first_metadata = first.messages[1]["metadata"]
    second_metadata = second.messages[1]["metadata"]
    assert second_metadata["stored_result_id"] == first_metadata["stored_result_id"]
    assert second_metadata["stored_result_path"] == first_metadata["stored_result_path"]
    assert second.transcript_refs == first.transcript_refs
    assert sorted(path.name for path in store.results_dir.iterdir()) == ["call-1.txt"]


def test_prepare_for_model_microcompacts_old_tool_results_only() -> None:
    state = RuntimeState()
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=100,
            microcompact_keep_recent=1,
        )
    )
    messages = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "old", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "old",
            "tool_name": "read_file",
            "content": "old content",
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "recent", "name": "read_file"}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "recent",
            "tool_name": "read_file",
            "content": "recent content",
        },
    )

    result = _prepare(service, messages, state)

    assert result.messages[1]["content"] == MICROCOMPACT_PLACEHOLDER
    assert result.messages[1]["metadata"]["microcompacted"] is True
    assert result.messages[3]["content"] == "recent content"


def test_prepare_method_can_be_used_as_context_preparer() -> None:
    state = RuntimeState()
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=100,
            microcompact_keep_recent=0,
        )
    )
    messages = (
        {"role": "tool_result", "tool_call_id": "call-1", "content": "content"},
    )

    prepared = asyncio.run(service.prepare(messages, state))

    assert prepared.messages[0]["content"] == MICROCOMPACT_PLACEHOLDER


def test_compaction_preparer_populates_context_snapshot_refs_and_hints(tmp_path) -> None:
    state = RuntimeState(session_id="session-compact")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_assistant(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "grep"}],
        }
    )
    message_store.append_tool_results(
        [
            ToolExecutionResult(
                tool_call_id="call-1",
                tool_name="grep",
                content="abcdef",
            )
        ]
    )
    store = ToolResultStorage(message_store.transcript_store.session_dir)
    service = ContextCompactionService(
        config=CompactionConfig(
            tool_result_budget_chars=3,
            tool_result_preview_chars=2,
            microcompact_keep_recent=1,
        ),
        result_store=store,
    )
    engine = ContextEngine(message_store, context_preparer=service)

    snapshot = asyncio.run(engine.build_for_model(state))

    assert snapshot.usage_hints["compaction_trigger"] == "micro"
    assert snapshot.usage_hints["token_after"] > 0
    assert snapshot.transcript_refs == (
        snapshot.messages[1]["metadata"]["stored_result_path"],
    )
