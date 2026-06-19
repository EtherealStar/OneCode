"""Tests for the streaming session's commit / flush behaviour (execplan §M4/§M5).

The streaming session's static-region commit path is now driven by
:class:`TerminalOutputCoordinator`. These tests pin down the
contract:

- The reducer (:func:`reduce_stream_event`) does not print tool
  results to the static region.
- The coordinator's ``flush_ready_checkpoints`` is the only path
  that writes pending tool commits to the static region.
- A tool result that has been committed is never re-printed by a
  later flush.
- Unknown / MCP tools still get a visible summary in the static
  region (no crash).
- Assistant text and tool results land in scrollback in the order
  they are checkpointed; the dynamic state no longer carries
  already-committed assistant text.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from core.stream_events import AgentEvent
from services.tools.types import ToolExecutionResult
from ui.cli.terminal import static_output as so
from ui.cli.terminal.output_coordinator import TerminalOutputCoordinator
from ui.cli.terminal.stream_reducer import reduce_stream_event
from ui.cli.terminal.stream_session import StreamingSession
from ui.cli.terminal.stream_state import (
    CliStreamUiState,
    CommitKind,
    StaticCommit,
    StreamingToolUseState,
    ToolStatus,
)
from ui.cli.theme import RICH_THEME
from rich.console import Console


def _evt(event_type: str, **kwargs) -> AgentEvent:
    return AgentEvent(type=event_type, **kwargs)


def _attr(call_id: str = "ac1", turn: int = 1, **extra) -> dict:
    metadata = {"assistant_call_id": call_id, "model_turn_index": turn}
    metadata.update(extra)
    return metadata


def _result(name: str, **metadata) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id="call_1",
        tool_name=name,
        content="x",
        metadata=metadata,
    )


def _captured_console() -> io.StringIO:
    """Build a fresh Rich console bound to a fresh StringIO."""

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


def test_coordinator_flush_prints_tool_result(tmp_path: Path) -> None:
    """The coordinator's flush is the only static-region commit path."""

    buffer = _captured_console()
    session = StreamingSession(workspace=tmp_path)
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("read_file", path=str(tmp_path / "a.py"), line_count=5),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    # The reducer must not have printed anything yet.
    assert buffer.getvalue() == ""
    # Drain pending commits into the coordinator and flush. This is
    # the new equivalent of the old ``_flush_completed_tools_to_static``.
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "[read_file] Read 5 line(s)" in output
    assert "⎿" in output or "a.py" in output  # container or path


def test_coordinator_flush_does_not_reprint(tmp_path: Path) -> None:
    """A second flush must not duplicate the line."""

    buffer = _captured_console()
    session = StreamingSession(workspace=tmp_path)
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=1),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    first_pass = buffer.getvalue()
    # A second flush must not duplicate the line. This protects the
    # scrollback from being polluted by repeated commits.
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    assert buffer.getvalue() == first_pass


def test_commit_final_drains_remaining_tool_results(tmp_path: Path) -> None:
    """A tool result that arrives after the live feed ends must still
    be committed to the scrollback by the coordinator.
    """

    buffer = _captured_console()
    state = CliStreamUiState()
    coord = TerminalOutputCoordinator()
    # Stand in for the streaming session by calling the coordinator
    # with a state that has the right fields populated.
    reduce_stream_event(
        state,
        _evt(
            "assistant_delta",
            text="final assistant text",
            metadata=_attr(),
        ),
    )
    # Commit the assistant text first via ``assistant_message_completed``;
    # otherwise streaming_text stays in the dynamic region.
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="final assistant text",
            metadata=_attr(),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=10),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    for commit in state.pending_static_commits:
        if commit.committed:
            continue
        coord.queue_commit(commit, workspace=tmp_path)
        commit.committed = True
    asyncio.run(coord.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "[read_file] Read 10 line(s)" in output
    # The assistant text is also committed.
    assert "final assistant text" in output


def test_commit_final_marks_completed_after_draining(tmp_path: Path) -> None:
    buffer = _captured_console()
    state = CliStreamUiState()
    coord = TerminalOutputCoordinator()
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=1),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    for commit in state.pending_static_commits:
        if commit.committed:
            continue
        coord.queue_commit(commit, workspace=tmp_path)
        commit.committed = True
    asyncio.run(coord.flush_ready_checkpoints())
    # Every staged result is now committed — a subsequent commit
    # must not re-print it.
    before = buffer.getvalue()
    # Queue and flush again to make sure we don't double-print.
    for commit in state.pending_static_commits:
        if commit.committed:
            continue
        coord.queue_commit(commit, workspace=tmp_path)
        commit.committed = True
    asyncio.run(coord.flush_ready_checkpoints())
    assert buffer.getvalue() == before


def test_unknown_tool_result_uses_fallback_summary(tmp_path: Path) -> None:
    buffer = _captured_console()
    session = StreamingSession(workspace=tmp_path)
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("some_mcp_tool"),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    output = buffer.getvalue()
    assert "some_mcp_tool" in output
    assert "call_1" in output


def test_static_output_container_prefix_is_added_by_framework(tmp_path: Path) -> None:
    """The ``⎿`` prefix must come from :func:`print_tool_result`, not
    from a tool-specific renderer.
    """

    buffer = _captured_console()
    session = StreamingSession(workspace=tmp_path)
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=1),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())
    output = buffer.getvalue()
    # The container prefix lives in the static output, not in the
    # renderer, so the output line is prefixed with the marker.
    assert "  ⎿" in output


def test_active_tool_count_reflects_new_state_model(tmp_path: Path) -> None:
    """The new state model's helpers must reflect running tools."""

    session = StreamingSession(workspace=tmp_path)
    # Pre-seed the new ``tools`` dict on the streaming state.
    session.state.tools["a"] = StreamingToolUseState(
        call_id="a", tool_name="a", status=ToolStatus.RUNNING
    )
    session.state.tools["b"] = StreamingToolUseState(
        call_id="b", tool_name="b", status=ToolStatus.RUNNING
    )
    assert session.state.active_tool_count() == 2
    # Mark one as completed via the reducer; the count should drop.
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="a", tool_name="a", content="ok"
            ),
            metadata=_attr(call_id="ac1", turn=1, tool_call_id="a"),
        ),
    )
    assert session.state.active_tool_count() == 1


# --- §M5: end-to-end order assertions ---------------------------------


def test_session_commits_assistant_tool_then_next_assistant_in_order(
    tmp_path: Path,
) -> None:
    """The scrollback order is: first assistant, tool result, second
    assistant. The first assistant's text is cleared from dynamic
    state after it has been committed.
    """

    buffer = _captured_console()
    session = StreamingSession(workspace=tmp_path)
    # First assistant message streams in and is committed on
    # ``assistant_message_completed``.
    reduce_stream_event(
        session.state,
        _evt("assistant_delta", text="first part", metadata=_attr()),
    )
    reduce_stream_event(
        session.state,
        _evt("assistant_message_completed", text="first part", metadata=_attr()),
    )
    assert session.state.streaming_text == ""
    # Tool result comes in next, releasing one tool_result commit.
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=1),
            metadata=_attr(tool_call_id="call_1"),
        ),
    )
    # Second round of assistant text uses a new turn id.
    reduce_stream_event(
        session.state,
        _evt(
            "assistant_delta",
            text="second part",
            metadata=_attr(call_id="ac2", turn=2),
        ),
    )
    reduce_stream_event(
        session.state,
        _evt(
            "assistant_message_completed",
            text="second part",
            metadata=_attr(call_id="ac2", turn=2),
        ),
    )
    # Drain everything to the static region.
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())

    output = buffer.getvalue()
    first_idx = output.find("first part")
    tool_idx = output.find("[read_file]")
    second_idx = output.find("second part")
    assert 0 <= first_idx < tool_idx < second_idx, (first_idx, tool_idx, second_idx)
    # First assistant text no longer lives in the dynamic state.
    assert "first part" not in session.state.streaming_text


def test_session_commits_carries_attribution_in_static_commits(
    tmp_path: Path,
) -> None:
    """Both assistant_markdown and tool_result commits must carry the
    same ``assistant_call_id`` so they can be matched in the scrollback.
    """

    session = StreamingSession(workspace=tmp_path)
    reduce_stream_event(
        session.state,
        _evt("assistant_delta", text="hi", metadata=_attr(call_id="ac_z", turn=3)),
    )
    reduce_stream_event(
        session.state,
        _evt(
            "assistant_message_completed",
            text="hi",
            metadata=_attr(call_id="ac_z", turn=3),
        ),
    )
    reduce_stream_event(
        session.state,
        _evt(
            "tool_result",
            result=_result("read_file", line_count=1),
            metadata=_attr(call_id="ac_z", turn=3, tool_call_id="call_1"),
        ),
    )
    session._commit_pending_to_coordinator()
    for commit in session.state.pending_static_commits:
        assert commit.assistant_call_id == "ac_z"
        assert commit.model_turn_index == 3
