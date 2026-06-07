from __future__ import annotations

import asyncio

from services.memory.auto_store import LongTermMemoryStore
from services.memory.extraction import (
    LongTermMemoryExtractionService,
    should_extract_long_term_memory,
)
from services.subagents.types import SubagentResult
from core.runtime_state import RuntimeState


class FakeRunner:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return SubagentResult(
            agent_type="fork",
            session_id="child",
            final_text="ok",
        )


def test_should_extract_skips_after_main_agent_memory_write():
    state = RuntimeState()
    state.turn_count = 3
    state.metadata["long_term_memory_writes"] = [{"turn_count": 3, "path": "x.md"}]

    decision = should_extract_long_term_memory(
        ({"role": "assistant", "content": "done"},),
        state,
        tool_calls=(),
    )

    assert decision == "main_agent_memory_write"


def test_extraction_runs_restricted_subagent(tmp_path):
    store = LongTermMemoryStore(tmp_path / "repo")
    runner = FakeRunner()
    state = RuntimeState()
    state.turn_count = 1
    service = LongTermMemoryExtractionService(store, subagent_runner=runner)

    asyncio.run(
        service.maybe_extract_after_model_response(
            (
                {"role": "user", "content": "remember this later"},
                {"role": "assistant", "content": "ok"},
            ),
            state,
            assistant_message={"role": "assistant", "content": "ok"},
            tool_calls=(),
        )
    )

    assert len(runner.requests) == 1
    request = runner.requests[0]
    assert request.metadata["purpose"] == "long_term_memory_extraction"
    assert request.metadata["allowed_memory_dir"].endswith(".onecode\\memory") or request.metadata[
        "allowed_memory_dir"
    ].endswith(".onecode/memory")
    assert state.metadata["long_term_memory_extraction"]["last_status"] == "success"
