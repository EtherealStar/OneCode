from __future__ import annotations

import asyncio
from pathlib import Path

from core.runtime_state import RuntimeState
from services.background_tasks import (
    BackgroundTaskManager,
    background_task_output_path,
    generate_background_task_id,
)


def test_background_task_id_prefixes_are_stable() -> None:
    assert generate_background_task_id("local_bash").startswith("b_")
    assert generate_background_task_id("local_agent").startswith("a_")
    assert generate_background_task_id("dream").startswith("d_")


def test_background_task_output_path_is_session_local(tmp_path: Path) -> None:
    path = background_task_output_path(tmp_path, "session-1", "b_1234")

    assert path == tmp_path / ".onecode" / "session-1" / "background-tasks" / "b_1234.output"


def test_background_agent_completion_drains_one_notification(tmp_path: Path) -> None:
    async def run() -> tuple[BackgroundTaskManager, RuntimeState]:
        manager = BackgroundTaskManager(workspace=tmp_path)
        state = RuntimeState(session_id="session-1")

        async def work(task_id: str) -> dict[str, object]:
            return {"summary": f"agent {task_id} done", "child_session_id": "child"}

        manager.start_agent(
            description="agent work",
            state=state,
            run=work,
            tool_use_id="call-1",
        )
        await asyncio.sleep(0)
        return manager, state

    manager, state = asyncio.run(run())

    notifications = manager.drain_notifications(state)

    assert len(notifications) == 1
    assert notifications[0]["task_type"] == "local_agent"
    assert notifications[0]["status"] == "completed"
    assert manager.drain_notifications(state) == ()


def test_stop_missing_and_terminal_task(tmp_path: Path) -> None:
    async def run() -> BackgroundTaskManager:
        manager = BackgroundTaskManager(workspace=tmp_path)
        state = RuntimeState(session_id="session-1")

        async def work(task_id: str) -> dict[str, object]:
            return {"summary": "done"}

        task = manager.start_agent(description="done", state=state, run=work)
        await asyncio.sleep(0)
        assert manager.stop(task.id).status == "completed"  # type: ignore[union-attr]
        return manager

    manager = asyncio.run(run())

    assert manager.stop("missing") is None
