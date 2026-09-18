from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.observability import ErrorLogRecorder, TraceRecorder
from services.permissions.types import PermissionResponse
from services.questions.types import QuestionResponse
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from ui.cli.batch import run_batch_async
from ui.cli.types import CliRuntime


class FakeLoop:
    def __init__(self) -> None:
        self.runtime: Any = None

    async def stream(
        self, prompt: str, *, attachments: Any = None
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(type="assistant_delta", text="hel")
        await asyncio.sleep(0)
        yield AgentEvent(type="assistant_delta", text="lo")
        yield AgentEvent(type="completed", text="hello")


class ToolLoop(FakeLoop):
    def __init__(self, workspace: Path) -> None:
        super().__init__()
        self.workspace = workspace

    async def stream(
        self, prompt: str, *, attachments: Any = None
    ) -> AsyncIterator[AgentEvent]:
        yield AgentEvent(
            type="tool_result",
            result=ToolExecutionResult(
                tool_call_id="call_read_1",
                tool_name="read_file",
                content="1\tcontent",
                metadata={
                    "path": str(self.workspace / "ui" / "cli" / "renderer.py"),
                    "offset": 1,
                    "line_count": 1,
                },
            ),
        )
        yield AgentEvent(type="completed", text="done")


class ProviderErrorLoop(FakeLoop):
    async def stream(
        self, prompt: str, *, attachments: Any = None
    ) -> AsyncIterator[AgentEvent]:
        raise RuntimeError("provider exploded")
        yield AgentEvent(type="completed", text="never")


class PermissionLoop(FakeLoop):
    def __init__(self) -> None:
        super().__init__()
        self.observed: PermissionResponse | None = None

    async def stream(
        self, prompt: str, *, attachments: Any = None
    ) -> AsyncIterator[AgentEvent]:
        response = await self.runtime.permission_prompter.request_permission(
            SimpleNamespace(tool_call=SimpleNamespace(id="c1"), options=())
        )
        self.observed = response
        yield AgentEvent(type="completed", text="done")


class QuestionLoop(FakeLoop):
    def __init__(self) -> None:
        super().__init__()
        self.observed: QuestionResponse | None = None

    async def stream(
        self, prompt: str, *, attachments: Any = None
    ) -> AsyncIterator[AgentEvent]:
        self.observed = await self.runtime.user_question_prompter.ask_questions(())
        yield AgentEvent(type="completed", text="done")


def _make_runtime(tmp_path: Path, loop: Any) -> CliRuntime:
    state = RuntimeState(session_id="session-batch")
    runtime = CliRuntime(
        workspace=tmp_path,
        state=state,
        message_store=MessageStore(
            transcript_root=tmp_path / ".onecode",
            session_id=state.session_id,
            flush_interval_seconds=60,
        ),
        registry=ToolRegistry(),
        loop=loop,
        provider_label="Fake",
        model="fake-model",
        model_client=object(),
        tool_executor=object(),  # type: ignore[arg-type]
        error_log_recorder=ErrorLogRecorder.noop(),
        trace_recorder=TraceRecorder.noop(),
    )
    loop.runtime = runtime
    return runtime


def _install(
    monkeypatch: Any,
    runtime: CliRuntime,
    line: Any = "hello",
) -> None:
    if line == "EOF":
        def raise_eof(prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("ui.cli.batch.read_batch_line", raise_eof)
    else:
        monkeypatch.setattr("ui.cli.batch.read_batch_line", lambda prompt="": line)
    monkeypatch.setattr("ui.cli.batch.build_runtime", lambda workspace: runtime)


def test_batch_streams_success(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    runtime = _make_runtime(tmp_path, FakeLoop())
    _install(monkeypatch, runtime)

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "Running..." in output
    assert "hello" in output


def test_batch_renders_tool_result(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    runtime = _make_runtime(tmp_path, ToolLoop(tmp_path))
    _install(monkeypatch, runtime)

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    output = capsys.readouterr().out.replace("\\", "/")
    assert "call_read_1" not in output
    assert "read_file" in output


def test_batch_provider_error_returns_nonzero(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    runtime = _make_runtime(tmp_path, ProviderErrorLoop())
    _install(monkeypatch, runtime)

    assert asyncio.run(run_batch_async(tmp_path)) == 1
    output = capsys.readouterr().out
    assert "provider exploded" in output


def test_batch_empty_input_does_not_start_turn(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    loop = FakeLoop()
    runtime = _make_runtime(tmp_path, loop)
    _install(monkeypatch, runtime, line="   ")

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    assert "Running..." not in capsys.readouterr().out


def test_batch_eof_is_clean_exit(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    runtime = _make_runtime(tmp_path, FakeLoop())
    _install(monkeypatch, runtime, line="EOF")

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    assert "Running..." not in capsys.readouterr().out


def test_batch_permission_deny_is_observed(
    tmp_path: Path, monkeypatch: Any
) -> None:
    loop = PermissionLoop()
    runtime = _make_runtime(tmp_path, loop)
    _install(monkeypatch, runtime)

    async def deny(self: Any, request: Any) -> PermissionResponse:
        return PermissionResponse(action="deny", feedback="no")

    monkeypatch.setattr(
        "ui.cli.batch.BatchPermissionPrompter.request_permission", deny
    )

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    assert loop.observed is not None
    assert loop.observed.action == "deny"


def test_batch_question_interrupt_is_structured(
    tmp_path: Path, monkeypatch: Any
) -> None:
    loop = QuestionLoop()
    runtime = _make_runtime(tmp_path, loop)
    _install(monkeypatch, runtime)

    async def decline(self: Any, questions: Any) -> QuestionResponse:
        return QuestionResponse(declined=True, feedback="interrupted")

    monkeypatch.setattr(
        "ui.cli.batch.BatchUserQuestionPrompter.ask_questions", decline
    )

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    assert loop.observed is not None
    assert loop.observed.declined is True


def test_batch_does_not_import_or_enter_tui(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    runtime = _make_runtime(tmp_path, FakeLoop())
    _install(monkeypatch, runtime)

    assert asyncio.run(run_batch_async(tmp_path)) == 0
    output = capsys.readouterr().out
    assert "\x1b[?1049h" not in output
    assert "\x1b[?1049l" not in output
    assert not any(
        name == "textual" or name.startswith("textual.")
        for name in sys.modules
    )
