from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from services.context.message_store import MessageStore
from services.tools.registry import ToolRegistry
from services.tools.types import ToolExecutionResult
from dataclasses import replace

from ui.cli.batch import run_batch_async
from ui.cli.types import CliRuntime


class FakeTty:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        return None


class FakeLoop:
    async def stream(
        self,
        prompt: str,
        *,
        attachments: object = None,
    ) -> AsyncIterator[AgentEvent]:
        assert prompt == "hello"
        assert attachments == ()
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(type="assistant_delta", text="hel")
        await asyncio.sleep(0)
        yield AgentEvent(type="assistant_delta", text="lo")
        yield AgentEvent(type="completed", text="hello")


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


class FakeToolLoop:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def stream(
        self,
        prompt: str,
        *,
        attachments: object = None,
    ) -> AsyncIterator[AgentEvent]:
        assert prompt == "hello"
        assert attachments == ()
        yield AgentEvent(type="interaction_started")
        yield AgentEvent(type="tool_result", result=ToolExecutionResult(
            tool_call_id="call_read_1",
            tool_name="read_file",
            content="1\tcontent",
            metadata={
                "path": str(self.workspace / "ui" / "cli" / "renderer.py"),
                "offset": 1,
                "line_count": 1,
            },
        ))
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


def test_batch_path_renders_streamed_delta(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    runtime = _make_runtime(tmp_path, FakeLoop())

    monkeypatch.setattr("ui.cli.batch.read_batch_line", lambda prompt="": "hello")
    monkeypatch.setattr("ui.cli.batch.build_runtime", lambda workspace: runtime)

    result = asyncio.run(run_batch_async(tmp_path))

    output = capsys.readouterr().out
    assert result == 0
    assert "Running..." in output
    assert "hello" in output


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


def test_batch_path_renders_tool_result_summary(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    runtime = _make_runtime(tmp_path, FakeToolLoop(tmp_path))

    monkeypatch.setattr("ui.cli.batch.read_batch_line", lambda prompt="": "hello")
    monkeypatch.setattr("ui.cli.batch.build_runtime", lambda workspace: runtime)

    result = asyncio.run(run_batch_async(tmp_path))

    output = capsys.readouterr().out.replace("\\", "/")
    assert result == 0
    assert "[read_file] Read 1 line(s) from ui/cli/renderer.py" in output
    assert "call_read_1" not in output


def test_main_uses_batch_when_stdin_is_not_tty(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from ui.cli import app as cli_app

    calls: list[Path] = []

    def fake_run_batch(workspace: Path) -> int:
        calls.append(workspace)
        return 42

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(False))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(True))
    monkeypatch.setattr("ui.cli.batch.run_batch", fake_run_batch)

    assert cli_app.main([]) == 42
    assert calls == [tmp_path]


def test_main_errors_when_stdout_is_not_tty(
    tmp_path: Path,
    monkeypatch: Any,
    capsys: Any,
) -> None:
    from ui.cli import app as cli_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(True))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(False))

    assert cli_app.main([]) == 1
    output = capsys.readouterr()
    assert "stdout is not a TTY" in output.err


def test_main_tty_builds_runtime_before_starting_repl(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    from ui.cli import app as cli_app

    runtime = _make_runtime(tmp_path, FakeLoop())
    calls: list[tuple[str, object]] = []

    class FakeInlineRepl:
        def __init__(
            self,
            app_runtime: CliRuntime,
            *,
            permission_prompter: object = None,
            interaction_host: object = None,
        ) -> None:
            assert permission_prompter is not None
            assert interaction_host is not None
            calls.append(("init", app_runtime))

        def run(self) -> int:
            calls.append(("run", None))
            return 0

    def fake_build_runtime(
        workspace: Path,
        *,
        trust_prompt: object = None,
        permission_prompter: object = None,
        mcp_trust_mode: str = "",
        **kwargs: object,
    ) -> CliRuntime:
        assert kwargs == {}
        assert workspace == tmp_path
        assert permission_prompter is not None
        assert trust_prompt is not None
        # The inline REPL prompts for MCP trust on startup (batch mode
        # still skips it).
        assert mcp_trust_mode == "prompt"
        calls.append(("build", permission_prompter))
        return runtime

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_app.sys, "stdin", FakeTty(True))
    monkeypatch.setattr(cli_app.sys, "stdout", FakeTty(True))
    monkeypatch.setattr(cli_app, "build_runtime", fake_build_runtime)
    monkeypatch.setattr("ui.cli.terminal.repl.InlineRepl", FakeInlineRepl)

    assert cli_app.main([]) == 0
    assert [name for name, _ in calls] == ["build", "init", "run"]
    assert calls[1] == ("init", runtime)
