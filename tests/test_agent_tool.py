from __future__ import annotations

import asyncio
import json

from core.runtime_state import RuntimeState
from services.subagents import SubagentResult
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall
from tools.agent import descriptor as agent_descriptor


class StubRunner:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return SubagentResult(
            agent_type=request.subagent_type or "fork",
            session_id="child-session",
            final_text="child summary",
            transition="completed",
            tool_result_count=2,
            metadata={"is_fork": request.subagent_type is None},
        )


def collect_result(executor: RegistryToolExecutor, call: ToolCall):
    async def run():
        results = []
        state = RuntimeState(session_id="parent-session")
        async for update in executor.execute((call,), state=state):
            if update.result is not None:
                results.append(update.result)
        return results[0]

    return asyncio.run(run())


def test_agent_tool_omitted_type_triggers_fork_request() -> None:
    runner = StubRunner()
    executor = RegistryToolExecutor(ToolRegistry([agent_descriptor(runner)]))

    result = collect_result(
        executor,
        ToolCall(id="call-agent", name="agent", input={"prompt": "continue"}),
    )

    assert runner.requests[0].subagent_type is None
    assert runner.requests[0].parent_tool_call_id == "call-agent"
    payload = json.loads(result.content)
    assert payload["agent_type"] == "fork"
    assert payload["is_fork"] is True
    assert payload["final_text"] == "child summary"


def test_agent_tool_explicit_type_uses_clean_subagent_request() -> None:
    runner = StubRunner()
    executor = RegistryToolExecutor(ToolRegistry([agent_descriptor(runner)]))

    result = collect_result(
        executor,
        ToolCall(
            id="call-agent",
            name="agent",
            input={"prompt": "search", "subagent_type": "general-purpose"},
        ),
    )

    assert runner.requests[0].subagent_type == "general-purpose"
    payload = json.loads(result.content)
    assert payload["agent_type"] == "general-purpose"
    assert payload["is_fork"] is False
