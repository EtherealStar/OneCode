from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.tools.registry import ToolRegistry
from ui.cli.app import main_loop_async
from ui.cli.types import CliRuntime


class FakeLoop:
    async def stream(self, prompt: str) -> AsyncIterator[AgentEvent]:
        assert prompt == "hello"
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(type="assistant_delta", text="hel")
        await asyncio.sleep(0)
        yield AgentEvent(type="assistant_delta", text="lo")
        yield AgentEvent(type="completed", text="hello")


def test_async_cli_renders_streamed_delta_before_exit(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    inputs = iter(["hello", "/exit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    monkeypatch.setattr("builtins.input", fake_input)
    state = RuntimeState(session_id="session-cli")
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=MessageStore(
            transcript_root=tmp_path / ".onecode",
            session_id=state.session_id,
            flush_interval_seconds=60,
        ),
        registry=ToolRegistry(),
        loop=FakeLoop(),  # type: ignore[arg-type]
        provider_label="Fake",
        model="fake-model",
        model_client=object(),
        tool_executor=object(),  # type: ignore[arg-type]
    )

    result = asyncio.run(main_loop_async(runtime))

    output = capsys.readouterr().out
    assert result == 0
    assert "Running..." in output
    assert "hello" in output
