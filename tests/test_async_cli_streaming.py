from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.tools.registry import ToolRegistry
from ui.cli.batch import run_batch_async
from ui.cli.types import CliRuntime


class FakeAttachmentCollector:
    async def collect_for_user_turn(self, prompt, state, messages, *, is_main_thread):
        assert prompt == "hello"
        assert is_main_thread is True
        return ({"role": "attachment", "attachment": {"type": "plan_mode"}},)


class FakeAttachmentLoop:
    async def stream(
        self,
        prompt: str,
        *,
        attachments: object = None,
    ) -> AsyncIterator[AgentEvent]:
        assert prompt == "hello"
        assert attachments == (
            {"role": "attachment", "attachment": {"type": "plan_mode"}},
        )
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(type="completed", text="done")


def _make_runtime(tmp_path: Path, loop: object) -> CliRuntime:
    state = RuntimeState(session_id="session-cli")
    return CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=MessageStore(
            transcript_root=tmp_path / ".onecode",
            session_id=state.session_id,
            flush_interval_seconds=60,
        ),
        registry=ToolRegistry(),
        loop=loop,  # type: ignore[arg-type]
        provider_label="Fake",
        model="fake-model",
        model_client=object(),
        tool_executor=object(),  # type: ignore[arg-type]
    )


def test_batch_path_collects_attachments_before_loop(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    runtime = replace(
        _make_runtime(tmp_path, FakeAttachmentLoop()),
        attachment_collector=FakeAttachmentCollector(),  # type: ignore[arg-type]
    )

    monkeypatch.setattr("ui.cli.batch.read_batch_line", lambda prompt="": "hello")
    monkeypatch.setattr("ui.cli.batch.build_runtime", lambda workspace: runtime)

    result = asyncio.run(run_batch_async(tmp_path))

    output = capsys.readouterr().out
    assert result == 0
    assert "done" in output
