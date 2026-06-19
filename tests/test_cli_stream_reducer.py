"""Tests for :mod:`ui.cli.terminal.stream_reducer` (execplan §M1/§M2/§M5).

These tests lock down the pure event-to-state mapping. They are
side-effect free: no stdout capture, no real Rich console, no
prompt_toolkit. The reducer's job is to mutate the
:class:`CliStreamUiState` in place; the tests assert the final
field values after a sequence of events.

Reducer 现在要求所有 assistant/tool 事件携带 ``assistant_call_id``
和 ``model_turn_index`` (execplan §M1);缺这些字段会切到 error
状态,而不是静默回退。
"""

from __future__ import annotations

from core.stream_events import AgentEvent
from services.tools.types import ToolExecutionResult
from ui.cli.terminal.stream_reducer import reduce_stream_event
from ui.cli.terminal.stream_state import (
    CliStreamUiState,
    StreamMode,
    ToolStatus,
)


def _evt(event_type: str, **kwargs) -> AgentEvent:
    """Shorthand for building an :class:`AgentEvent`."""

    return AgentEvent(type=event_type, **kwargs)


def _attr(call_id: str = "ac1", turn: int = 1, **extra) -> dict:
    """Standard attribution metadata for a model turn event."""

    metadata = {"assistant_call_id": call_id, "model_turn_index": turn}
    metadata.update(extra)
    return metadata


class _Call:
    """Stand-in for a ``ToolCall`` (dataclass with id/name/input)."""

    def __init__(self, *, id: str, name: str, input: dict | None = None) -> None:
        self.id = id
        self.name = name
        self.input = input or {}


# --- assistant delta accumulation -----------------------------------------


def test_assistant_deltas_append_in_order() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="Hello ", metadata=_attr()),
    )
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="world", metadata=_attr()),
    )
    assert state.streaming_text == "Hello world"


def test_assistant_delta_switches_mode_to_responding() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="hi", metadata=_attr()),
    )
    assert state.stream_mode == StreamMode.RESPONDING


def test_completed_event_emits_final_assistant_checkpoint() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("completed", text="final answer", metadata=_attr()),
    )
    # The completed event without prior deltas seeds ``streaming_text``
    # and then immediately stages a checkpoint, clearing the buffer.
    assert state.streaming_text == ""
    assert state.turn_completed is True
    assert state.stream_mode == StreamMode.COMPLETED
    assert any(c.is_assistant_markdown for c in state.pending_static_commits)


def test_completed_event_does_not_clobber_existing_checkpoint() -> None:
    """When deltas already fed the buffer and a checkpoint was emitted
    on ``assistant_message_completed``, ``completed`` should not emit
    a duplicate ``assistant_markdown`` commit.
    """

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="partial ", metadata=_attr()),
    )
    reduce_stream_event(
        state,
        _evt("assistant_message_completed", text="partial ", metadata=_attr()),
    )
    commits_after_completion = list(state.pending_static_commits)
    reduce_stream_event(
        state,
        _evt("completed", text="partial ", metadata=_attr()),
    )
    # No new commit; only the original one survives.
    assert state.pending_static_commits == commits_after_completion
    assert state.streaming_text == ""


# --- tool lifecycle ------------------------------------------------------


def test_tool_call_ready_creates_queued_tool() -> None:
    state = CliStreamUiState()
    call = _Call(id="c1", name="read_file", input={"path": "core/loop.py"})
    reduce_stream_event(
        state,
        _evt("tool_call_ready", metadata=_attr(tool_call=call)),
    )
    assert "c1" in state.tools
    tool = state.tools["c1"]
    assert tool.tool_name == "read_file"
    assert tool.status == ToolStatus.QUEUED
    assert "path=" in tool.input_preview


def test_tool_call_ready_without_call_id_is_ignored() -> None:
    """Some providers omit the id on the streaming event; we ignore it."""

    state = CliStreamUiState()
    call = _Call(id="", name="bash")
    reduce_stream_event(
        state,
        _evt("tool_call_ready", metadata=_attr(tool_call=call)),
    )
    assert state.tools == {}


def test_tool_started_promotes_tool_to_running() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="c1", name="bash")),
        ),
    )
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="bash")),
    )
    assert state.tools["c1"].status == ToolStatus.RUNNING
    assert state.stream_mode == StreamMode.TOOL_RUNNING


def test_tool_started_without_prior_ready_creates_tool() -> None:
    """``tool_started`` arriving alone must still register the tool."""

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="bash")),
    )
    assert state.tools["c1"].status == ToolStatus.RUNNING


def test_tool_progress_records_message() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="c1", name="bash")),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_progress",
            metadata=_attr(tool_call_id="c1", message="scanning…"),
        ),
    )
    assert state.tools["c1"].progress == "scanning…"


def test_tool_progress_with_unknown_call_id_is_ignored() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_progress",
            metadata=_attr(call_id="ac2", turn=2, tool_call_id="ghost", message="noise"),
        ),
    )
    assert state.tools == {}


def test_tool_call_delta_attaches_name_to_first_unnamed_tool() -> None:
    state = CliStreamUiState()
    call = _Call(id="c1", name="")
    reduce_stream_event(
        state,
        _evt("tool_call_ready", metadata=_attr(tool_call=call)),
    )
    reduce_stream_event(
        state,
        _evt("tool_call_delta", metadata=_attr(name="bash")),
    )
    assert state.tools["c1"].tool_name == "bash"


def test_tool_result_releases_commit_with_declaration_index() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="c1", name="read_file")),
        ),
    )
    result = ToolExecutionResult(
        tool_call_id="c1",
        tool_name="read_file",
        content="contents",
    )
    reduce_stream_event(
        state,
        _evt("tool_result", result=result, metadata=_attr(tool_call_id="c1")),
    )
    assert "c1" not in state.tools
    assert len(state.pending_static_commits) == 1
    commit = state.pending_static_commits[0]
    assert commit.is_tool_result
    assert commit.declared_index == 0
    assert commit.assistant_call_id == "ac1"
    # The reducer must NOT mark it committed; that's the session's job.
    assert commit.committed is False


def test_tool_result_with_more_active_tools_keeps_running_mode() -> None:
    """Two tools running; one finishes; we should still be in TOOL_RUNNING."""

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="bash")),
    )
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c2", tool_name="grep")),
    )
    result = ToolExecutionResult(tool_call_id="c1", tool_name="bash", content="ok")
    reduce_stream_event(
        state,
        _evt("tool_result", result=result, metadata=_attr(tool_call_id="c1")),
    )
    assert state.stream_mode == StreamMode.TOOL_RUNNING


def test_tool_result_with_no_active_tools_switches_to_awaiting_model() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="bash")),
    )
    result = ToolExecutionResult(tool_call_id="c1", tool_name="bash", content="ok")
    reduce_stream_event(
        state,
        _evt("tool_result", result=result, metadata=_attr(tool_call_id="c1")),
    )
    assert state.stream_mode == StreamMode.AWAITING_MODEL


# --- assistant_message_completed / completed / error --------------------


def test_assistant_message_completed_sets_flag_and_keeps_mode() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="hello", metadata=_attr()),
    )
    reduce_stream_event(
        state,
        _evt("assistant_message_completed", text="hello", metadata=_attr()),
    )
    assert state.assistant_completed is True
    # No active tools, so we move to awaiting_model.
    assert state.stream_mode == StreamMode.AWAITING_MODEL
    # ``streaming_text`` is cleared by the checkpoint submission.
    assert state.streaming_text == ""


def test_assistant_message_completed_with_active_tools_keeps_running_mode() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="bash")),
    )
    reduce_stream_event(
        state,
        _evt("assistant_message_completed", text="hi", metadata=_attr()),
    )
    assert state.assistant_completed is True
    assert state.stream_mode == StreamMode.TOOL_RUNNING


def test_error_event_sets_text_and_mode() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("error", text="boom"))
    assert state.error_text == "boom"
    assert state.stream_mode == StreamMode.ERROR


def test_terminal_mode_is_not_overwritten_by_later_events() -> None:
    """``completed`` / ``error`` are terminal; later deltas don't change mode."""

    state = CliStreamUiState()
    reduce_stream_event(state, _evt("completed", text="", metadata=_attr()))
    assert state.stream_mode == StreamMode.COMPLETED
    # A late event missing attribution should not flip the terminal mode.
    reduce_stream_event(state, _evt("assistant_delta", text="late"))
    assert state.stream_mode == StreamMode.COMPLETED


# --- reducer purity ------------------------------------------------------


def test_reducer_does_not_print(capsys) -> None:
    """The reducer must not produce stdout/stderr output."""

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="c1", name="read_file")),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="c1",
                tool_name="read_file",
                content="x",
            ),
            metadata=_attr(tool_call_id="c1"),
        ),
    )
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ""


def test_reducer_ignores_unknown_event_types() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("transition", text="tool_use"))
    reduce_stream_event(state, _evt("interaction_started"))
    assert state.streaming_text == ""
    assert state.tools == {}
    assert state.pending_static_commits == []


# --- end-to-end turn lifecycle -----------------------------------------


def test_full_turn_lifecycle_assistant_then_tool_then_assistant() -> None:
    """A typical model turn: assistant text, tool call, tool result, more text.

    整个事件流必须共享 ``assistant_call_id``;reducer 在
    ``assistant_message_completed`` 时清空 ``streaming_text``,
    让下一段 assistant 文本从空动态区开始。
    """

    state = CliStreamUiState()
    # 1) Assistant starts streaming.
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="Let me read that file.\n", metadata=_attr()),
    )
    assert state.stream_mode == StreamMode.RESPONDING
    # 2) Model emits a tool call.
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(
                tool_call=_Call(id="c1", name="read_file", input={"path": "core/loop.py"})
            ),
        ),
    )
    assert state.tools["c1"].status == ToolStatus.QUEUED
    # 3) Tool starts running.
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata=_attr(tool_call_id="c1", tool_name="read_file"),
        ),
    )
    assert state.tools["c1"].status == ToolStatus.RUNNING
    # 4) Tool returns a result.
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="c1",
                tool_name="read_file",
                content="...",
            ),
            metadata=_attr(tool_call_id="c1"),
        ),
    )
    assert "c1" not in state.tools
    assert len(state.pending_static_commits) == 1
    assert state.pending_static_commits[0].committed is False
    # 5) Assistant message checkpoint: streaming_text cleared.
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="Let me read that file.\n",
            metadata=_attr(),
        ),
    )
    assert state.streaming_text == ""
    assert any(c.is_assistant_markdown for c in state.pending_static_commits)
    # 6) Next round: new turn id, fresh streaming text.
    reduce_stream_event(
        state,
        _evt(
            "assistant_delta",
            text="The file has 200 lines.",
            metadata=_attr(call_id="ac2", turn=2),
        ),
    )
    assert state.streaming_text == "The file has 200 lines."
    assert state.current_assistant_call_id == "ac2"


def test_active_tool_helpers_filter_and_limit() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c1", tool_name="read_file")),
    )
    reduce_stream_event(
        state,
        _evt("tool_started", metadata=_attr(tool_call_id="c2", tool_name="")),
    )
    visible = state.visible_active_tools(limit=10)
    assert [t.call_id for t in visible] == ["c1"]
    # The visible helper filters out tools without a name, but
    # ``active_tool_count`` counts every queued/running tool — even
    # one whose ``tool_name`` arrived empty. That distinction matters
    # when the view decides whether the status line should switch
    # away from the bare ``thinking…`` indicator.
    assert state.active_tool_count() == 2


def test_visible_active_tools_respects_limit() -> None:
    state = CliStreamUiState()
    for i in range(3):
        reduce_stream_event(
            state,
            _evt(
                "tool_started",
                metadata=_attr(
                    call_id=f"ac{i}", turn=i + 1, tool_call_id=f"c{i}", tool_name=f"tool{i}"
                ),
            ),
        )
    visible = state.visible_active_tools(limit=2)
    assert [t.call_id for t in visible] == ["c0", "c1"]
    assert state.overflow_active_count(limit=2) == 1
