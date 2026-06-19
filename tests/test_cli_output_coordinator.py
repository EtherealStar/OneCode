"""Tests for :mod:`ui.cli.terminal.output_coordinator` (execplan §M3/§M5).

The coordinator's contract is that ``queue_*`` methods never write
to stdout; only async ``flush_ready_checkpoints`` does. The tests
capture a Rich console on a StringIO and assert that:

- ``queue_commit`` does not produce any output on its own.
- Multiple commits are flushed in insertion order.
- A second flush is a no-op until more commits are queued.
- ``queue_status_line`` defers the same way and writes the line on
  the next flush.
- duplicate commits (same ``assistant_call_id`` and ``sequence``) are
  silently dropped so retrying does not re-print.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from rich.console import Console
from rich.text import Text

from ui.cli.terminal import static_output as so
from ui.cli.terminal.output_coordinator import TerminalOutputCoordinator
from ui.cli.terminal.stream_state import CommitKind, StaticCommit
from ui.cli.theme import RICH_THEME


def _captured_console() -> io.StringIO:
    """Bind the module-level static console to a captured buffer."""

    buffer = io.StringIO()
    so.reset_static_console()
    so._STATIC_CONSOLE = Console(  # noqa: SLF001
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=80,
        theme=RICH_THEME,
    )
    return buffer


def _make_commit(
    *, kind: str, payload, assistant_call_id: str = "ac1", sequence: int = 0
) -> StaticCommit:
    return StaticCommit(
        sequence=sequence,
        kind=kind,
        payload=payload,
        model_turn_index=1,
        assistant_call_id=assistant_call_id,
    )


# --- queue does not write ------------------------------------------------


def test_queue_commit_does_not_write_to_static() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    commit = _make_commit(kind=CommitKind.TOOL_RESULT, payload=object())
    coord.queue_commit(commit)
    # The commit is staged but not printed.
    assert buffer.getvalue() == ""
    assert coord.pending_commit_count() == 1


def test_queue_status_line_does_not_write_to_static() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_status_line("已取消")
    assert buffer.getvalue() == ""
    assert coord.pending_status_line_count() == 1


def test_queue_assistant_markdown_via_commit_does_not_write() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_commit(
        _make_commit(kind=CommitKind.ASSISTANT_MARKDOWN, payload="hello")
    )
    assert buffer.getvalue() == ""
    assert coord.pending_commit_count() == 1


# --- flush produces the expected output --------------------------------


def test_flush_writes_tool_result(tmp_path: Path) -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    from services.tools.types import ToolExecutionResult

    result = ToolExecutionResult(
        tool_call_id="c1",
        tool_name="read_file",
        content="x",
        metadata={"line_count": 1, "path": "a.py"},
    )
    coord.queue_commit(_make_commit(kind=CommitKind.TOOL_RESULT, payload=result))
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "[read_file]" in output
    # The container prefix is added by ``print_tool_result``.
    assert "  ⎿" in output
    # After flush, the queue is drained.
    assert coord.pending_commit_count() == 0


def test_flush_preserves_queue_order() -> None:
    """Two tool results must flush in the order they were queued."""

    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    from services.tools.types import ToolExecutionResult

    first = ToolExecutionResult(
        tool_call_id="c1", tool_name="read_file", content="x", metadata={"line_count": 1}
    )
    second = ToolExecutionResult(
        tool_call_id="c2", tool_name="bash", content="y", metadata={"command": "ls"}
    )
    coord.queue_commit(_make_commit(kind=CommitKind.TOOL_RESULT, payload=first, sequence=0))
    coord.queue_commit(_make_commit(kind=CommitKind.TOOL_RESULT, payload=second, sequence=1))
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    # ``read_file`` is queued first, so its line must appear before
    # ``bash`` in the captured output.
    assert output.find("[read_file]") < output.find("bash")


def test_flush_writes_tool_results_before_assistant_markdown() -> None:
    """Tool results should land in scrollback before the assistant reply."""

    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    from services.tools.types import ToolExecutionResult

    coord.queue_commit(
        _make_commit(
            kind=CommitKind.TOOL_RESULT,
            payload=ToolExecutionResult(
                tool_call_id="c1",
                tool_name="read_file",
                content="x",
                metadata={"line_count": 1},
            ),
            sequence=0,
        )
    )
    coord.queue_commit(
        _make_commit(
            kind=CommitKind.ASSISTANT_MARKDOWN,
            payload="done",
            sequence=1,
        )
    )
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    # The tool line must precede the assistant's "done" in the
    # captured output so scrollback reads top-to-bottom in event
    # order.
    assert output.find("[read_file]") < output.find("done")


def test_flush_writes_status_line_after_other_commits() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_commit(_make_commit(kind=CommitKind.ASSISTANT_MARKDOWN, payload="done"))
    coord.queue_status_line("已取消")
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "done" in output
    assert "已取消" in output
    assert output.find("done") < output.find("已取消")


def test_second_flush_is_noop_until_more_commits_are_queued() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_status_line("line")
    asyncio.run(coord.flush_ready_checkpoints())
    first = buffer.getvalue()
    # A second flush must be a no-op: nothing is queued.
    asyncio.run(coord.flush_ready_checkpoints())
    assert buffer.getvalue() == first
    # Once we queue again, the new commit does land.
    coord.queue_status_line("second")
    asyncio.run(coord.flush_ready_checkpoints())
    second = buffer.getvalue()
    assert "second" in second
    assert second.count("line") == 1


# --- status_line accepts both string and Text renderable --------------


def test_status_line_accepts_rich_text() -> None:
    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_status_line(Text("cancelled", style="onecode.warning"))
    asyncio.run(coord.flush_ready_checkpoints())
    assert "cancelled" in buffer.getvalue()


# --- lifecycle markers route through prompt_toolkit --------------------


def test_dynamic_app_marker_flushes_through_run_in_terminal(monkeypatch) -> None:
    """Dynamic flushes must suspend prompt_toolkit before printing."""

    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_status_line("hello")
    calls: list[str] = []

    async def fake_run_in_terminal(func, render_cli_done=False, in_executor=False):
        calls.append(
            f"render_cli_done={render_cli_done};in_executor={in_executor}"
        )
        return func()

    import ui.cli.terminal.output_coordinator as coordinator_module

    monkeypatch.setattr(coordinator_module, "run_in_terminal", fake_run_in_terminal)
    coord.begin_dynamic_app()
    asyncio.run(coord.flush_ready_checkpoints())

    assert "hello" in buffer.getvalue()
    assert calls == ["render_cli_done=False;in_executor=False"]

    coord.end_dynamic_app()
    coord.queue_status_line("after")
    asyncio.run(coord.flush_ready_checkpoints())
    assert "after" in buffer.getvalue()
    assert buffer.getvalue().count("hello") == 1
    # The post-dynamic flush writes directly, without another
    # prompt_toolkit suspension.
    assert calls == ["render_cli_done=False;in_executor=False"]


# --- dedup based on (assistant_call_id, sequence) ----------------------


def test_duplicate_commit_is_dropped() -> None:
    """Re-queuing a commit with the same id is a no-op (idempotent)."""

    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    commit = _make_commit(
        kind=CommitKind.ASSISTANT_MARKDOWN,
        payload="once",
        assistant_call_id="ac1",
        sequence=42,
    )
    coord.queue_commit(commit)
    # A second queue of the same commit (e.g. retry) is silently
    # dropped so the line is not re-printed.
    coord.queue_commit(commit)
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert output.count("once") == 1


def test_checkpoint_flush_writes_immediately_after_queue() -> None:
    """A commit queued then flushed is written to scrollback at once."""

    buffer = _captured_console()
    coord = TerminalOutputCoordinator()
    coord.queue_commit(
        _make_commit(kind=CommitKind.ASSISTANT_MARKDOWN, payload="checkpoint")
    )
    asyncio.run(coord.flush_ready_checkpoints())
    # The static region already shows the assistant text — no need
    # to wait until "turn end".
    assert "checkpoint" in buffer.getvalue()
