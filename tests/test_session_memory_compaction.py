from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.compaction import (
    ContextCompactionService,
    SessionMemory,
    SessionMemoryStore,
    SessionMemoryUpdater,
)
from services.compaction.types import CompactionConfig, CompactionTrigger
from services.context.message_store import MessageStore
from services.subagents.types import SubagentRequest, SubagentResult


@dataclass
class FakeSubagentRunner:
    requests: list[SubagentRequest] = field(default_factory=list)

    async def run(self, request: SubagentRequest) -> SubagentResult:
        self.requests.append(request)
        return SubagentResult(
            agent_type="fork",
            session_id="child-compact",
            final_text="<analysis>hidden</analysis><summary>Compacted summary.</summary>",
            metadata={"is_fork": True},
        )


def run(coro):
    return asyncio.run(coro)


def test_session_memory_updater_writes_single_markdown_file(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-memory")
    state.turn_count = 3
    store = SessionMemoryStore(tmp_path / ".onecode" / state.session_id)
    updater = SessionMemoryUpdater(store)
    messages = (
        {"role": "user", "content": "Implement compaction"},
        {"role": "assistant", "content": "Added memory support"},
    )

    run(updater.update_after_turn(messages, state))

    assert store.path.exists()
    assert not (store.path.parent / "session-memory.json").exists()
    memory = store.read()
    assert memory is not None
    assert memory.covered_turn_count == 3
    assert memory.last_summarized_message_uuid == "message-2"
    assert "## Current Goal" in memory.content
    assert "Implement compaction" in memory.content


def test_session_memory_compact_rewrites_active_chain_before_full_compact(
    tmp_path: Path,
) -> None:
    state = RuntimeState(session_id="session-compact-memory")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    for index in range(4):
        message_store.append_user(f"old user message {index} " + ("x" * 1000))
        message_store.append_assistant({"role": "assistant", "content": "old assistant"})
    memory_store = SessionMemoryStore(message_store.transcript_store.session_dir)
    memory_store.write(
        SessionMemory(
            content="# Session Memory\n\n## Current Goal\nKeep compacting.",
            last_summarized_message_uuid="message-8",
            updated_at="2026-06-06T00:00:00+00:00",
            covered_turn_count=4,
        )
    )
    runner = FakeSubagentRunner()
    service = ContextCompactionService(
        config=CompactionConfig(
            default_context_window_tokens=1_000,
            summary_output_reserved_tokens=100,
            auto_compact_buffer_tokens=300,
            session_memory_min_tokens=1,
            session_memory_max_tokens=80,
            session_memory_min_text_messages=1,
        ),
        message_store=message_store,
        session_memory_store=memory_store,
        subagent_runner=runner,
    )

    result = run(service.maybe_auto_compact(message_store.current_messages(), state))

    assert result is not None
    assert result.trigger == CompactionTrigger.AUTO_SESSION_MEMORY
    assert runner.requests == []
    messages = message_store.current_messages()
    assert messages[0]["metadata"]["is_compact_boundary"] is True
    assert "Session Memory" in messages[1]["content"]
    assert state.metadata["last_compaction"]["trigger"] == "auto_session_memory"


def test_full_compact_uses_implicit_fork_subagent(tmp_path: Path) -> None:
    state = RuntimeState(session_id="session-full-compact")
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    message_store.append_user("Summarize this long session")
    runner = FakeSubagentRunner()
    service = ContextCompactionService(
        message_store=message_store,
        subagent_runner=runner,
    )

    result = run(service.manual_compact(state, focus="current task"))

    assert result.trigger == CompactionTrigger.MANUAL
    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.subagent_type is None
    assert request.parent_session_id == state.session_id
    assert "Do not call tools" in request.prompt
    messages = message_store.current_messages()
    assert "Compacted summary." in messages[1]["content"]
    assert "hidden" not in messages[1]["content"]
