from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.runtime_state import RuntimeState
from services.background_tasks import BackgroundTaskManager
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
            tool_result_count=1,
            metadata={"is_fork": request.subagent_type is None},
        )


def test_background_agent_returns_immediately_and_notifies(tmp_path: Path) -> None:
    async def run():
        runner = StubRunner()
        manager = BackgroundTaskManager(workspace=tmp_path)
        executor = RegistryToolExecutor(
            ToolRegistry([agent_descriptor(runner, manager)])
        )
        state = RuntimeState(session_id="parent-session")
        results = []
        async for update in executor.execute(
            (
                ToolCall(
                    id="call-agent",
                    name="agent",
                    input={"prompt": "work", "run_in_background": True},
                ),
            ),
            state,
        ):
            if update.result is not None:
                results.append(update.result)
        await asyncio.sleep(0)
        return runner, manager, state, results[0]

    runner, manager, state, result = asyncio.run(run())

    payload = json.loads(result.content)
    assert payload["task_id"].startswith("a_")
    assert payload["status"] == "running"
    assert runner.requests[0].metadata["background_task_id"] == payload["task_id"]
    notifications = manager.drain_notifications(state)
    assert len(notifications) == 1
    assert notifications[0]["task_id"] == payload["task_id"]
