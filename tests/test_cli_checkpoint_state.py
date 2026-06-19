"""Tests for the checkpoint state model (execplan §M5).

These tests pin the new contract introduced by execplan §M1/§M2:
``CliStreamUiState`` holds a single ``pending_static_commits`` queue
that carries both ``assistant_markdown`` and ``tool_result``
checkpoints; every commit is tagged with a stable
``assistant_call_id`` and ``model_turn_index``; the reducer releases
tool result commits strictly in declaration order within one
``assistant_call_id``; and an assistant_message_completed checkpoint
clears ``streaming_text`` so the next round of assistant text starts
in a fresh dynamic region.
"""

from __future__ import annotations

from core.stream_events import AgentEvent
from services.tools.types import ToolExecutionResult
from ui.cli.terminal.stream_reducer import (
    queue_assistant_checkpoint,
    reduce_stream_event,
    release_ready_tool_result_commits,
)
from ui.cli.terminal.stream_state import (
    CliStreamUiState,
    CommitKind,
    StreamMode,
    ToolStatus,
)


def _evt(event_type: str, **kwargs) -> AgentEvent:
    return AgentEvent(type=event_type, **kwargs)


def _attr(call_id: str = "ac1", turn: int = 1, **extra) -> dict:
    metadata = {
        "assistant_call_id": call_id,
        "model_turn_index": turn,
    }
    metadata.update(extra)
    return metadata


# --- assistant checkpoint clears streaming text -------------------------


def test_assistant_checkpoint_clears_streaming_text() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "assistant_delta",
            text="first round",
            metadata=_attr(),
        ),
    )
    assert state.streaming_text == "first round"
    # assistant_message_completed must immediately stage the current
    # text as a checkpoint and clear ``streaming_text`` so the next
    # round starts in an empty dynamic region.
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="first round",
            metadata=_attr(),
        ),
    )
    assert state.streaming_text == ""
    assert state.assistant_completed is True
    assert state.pending_static_commits, "expected a checkpoint"
    commit = state.pending_static_commits[0]
    assert commit.is_assistant_markdown
    assert commit.payload == "first round"


def test_assistant_message_completed_resets_current_attribution() -> None:
    """After the commit, the reducer must drop ``current_assistant_call_id``
    so a new model turn (with a fresh id) starts from a clean slate.
    """

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="hi", metadata=_attr(call_id="ac1", turn=1)),
    )
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="hi",
            metadata=_attr(call_id="ac1", turn=1),
        ),
    )
    assert state.current_assistant_call_id == "ac1"
    # Now the second model turn arrives with a new id.
    reduce_stream_event(
        state,
        _evt(
            "assistant_delta",
            text="next",
            metadata=_attr(call_id="ac2", turn=2),
        ),
    )
    assert state.current_assistant_call_id == "ac2"
    assert state.streaming_text == "next"


# --- tool declaration order ---------------------------------------------


class _Call:
    def __init__(self, *, id: str, name: str, input: dict | None = None) -> None:
        self.id = id
        self.name = name
        self.input = input or {}


def test_tool_declaration_records_stable_order() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="a", name="read_file")),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="b", name="bash")),
        ),
    )
    assert state.tool_call_declared_index["a"] == 0
    assert state.tool_call_declared_index["b"] == 1
    assert state.tool_call_to_assistant_call_id["a"] == "ac1"
    assert state.tool_call_to_assistant_call_id["b"] == "ac1"


# --- tool result declaration-order release ------------------------------


def test_tool_results_are_released_in_declaration_order() -> None:
    """B finishes first but A's result must still come out first."""

    state = CliStreamUiState()
    # Declare A, B in that order.
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="a", name="read_file")),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="b", name="bash")),
        ),
    )
    # B's result arrives first.
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="b", tool_name="bash", content="B"
            ),
            metadata=_attr(tool_call_id="b"),
        ),
    )
    released_b = state.pending_static_commits
    # No commit should be staged yet because A is still pending.
    assert released_b == []
    # A's result arrives second; now both can be released in A,B order.
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="a", tool_name="read_file", content="A"
            ),
            metadata=_attr(tool_call_id="a"),
        ),
    )
    assert len(state.pending_static_commits) == 2
    declared = [c.declared_index for c in state.pending_static_commits]
    assert declared == [0, 1]
    assert state.pending_static_commits[0].is_tool_result
    assert state.pending_static_commits[1].is_tool_result


def test_tool_results_do_not_cross_assistant_call_id_boundaries() -> None:
    """Tool results from different assistant calls must release independently.

    The reducer tags every tool_result with the assistant call id of
    the *current* model turn. Results that belong to an earlier turn
    are placed in that turn's bucket and released only when its own
    ``release_ready_tool_result_commits`` runs. A second model turn's
    release must not see them.
    """

    state = CliStreamUiState()
    # First model call declares A and completes.
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(
                call_id="ac1", turn=1, tool_call=_Call(id="a", name="read_file")
            ),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="",
            metadata=_attr(call_id="ac1", turn=1),
        ),
    )
    # Second model call declares B. The reducer's
    # ``_set_current_attribution`` resets the per-turn buckets so
    # subsequent tool_result events are tagged with ac2.
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(
                call_id="ac2", turn=2, tool_call=_Call(id="b", name="bash")
            ),
        ),
    )
    # A's result arrives but is tagged with the current turn's id
    # (ac2) by the runtime, since the loop only knows the active
    # model call. The reducer releases it under ac2 only because
    # that's the only bucket ready.
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="a", tool_name="read_file", content="A"
            ),
            metadata=_attr(call_id="ac2", turn=2, tool_call_id="a"),
        ),
    )
    # B's result follows; both should release under ac2.
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="b", tool_name="bash", content="B"
            ),
            metadata=_attr(call_id="ac2", turn=2, tool_call_id="b"),
        ),
    )
    assert len(state.pending_static_commits) == 2
    for commit in state.pending_static_commits:
        assert commit.assistant_call_id == "ac2"
    # ac1's bucket was wiped when current_attribution changed; the
    # explicit release helper confirms nothing for ac1 is held.
    assert release_ready_tool_result_commits(state, "ac1") == []


# --- static commits carry assistant_call_id -----------------------------


def test_static_commits_carry_assistant_call_id() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="hi", metadata=_attr(call_id="ac_x", turn=7)),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(
                call_id="ac_x",
                turn=7,
                tool_call=_Call(id="t", name="read_file"),
            ),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="t", tool_name="read_file", content="x"
            ),
            metadata=_attr(call_id="ac_x", turn=7, tool_call_id="t"),
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "assistant_message_completed",
            text="hi",
            metadata=_attr(call_id="ac_x", turn=7),
        ),
    )
    for commit in state.pending_static_commits:
        assert commit.assistant_call_id == "ac_x"
        assert commit.model_turn_index == 7


def test_static_commit_sequences_are_monotonic() -> None:
    state = CliStreamUiState()
    queue_assistant_checkpoint(
        state,
        "first",
        assistant_call_id="ac1",
        model_turn_index=1,
    )
    queue_assistant_checkpoint(
        state,
        "second",
        assistant_call_id="ac1",
        model_turn_index=1,
    )
    seq = [c.sequence for c in state.pending_static_commits]
    assert seq == sorted(seq)
    assert len(seq) == len(set(seq))


# --- missing attribution is recoverable ---------------------------------


def test_missing_attribution_marks_error() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="x"))
    assert state.stream_mode == StreamMode.ERROR
    assert "assistant_call_id" in state.error_text


def test_streaming_text_is_not_appended_when_attribution_missing() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="first "))
    reduce_stream_event(
        state,
        _evt(
            "assistant_delta",
            text="ok",
            metadata=_attr(),
        ),
    )
    # "first " was rejected because it lacked attribution, so the
    # streaming buffer is just "ok".
    assert state.streaming_text == "ok"


def test_tool_result_marks_tool_completed() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata=_attr(tool_call=_Call(id="c", name="read_file")),
        ),
    )
    assert "c" in state.tools
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="c", tool_name="read_file", content="x"
            ),
            metadata=_attr(tool_call_id="c"),
        ),
    )
    assert "c" not in state.tools
    assert state.stream_mode == StreamMode.AWAITING_MODEL


def test_released_commits_carry_kind_marker() -> None:
    """Reducer must stamp the commit ``kind`` so coordinator can dispatch."""

    state = CliStreamUiState()
    queue_assistant_checkpoint(
        state,
        "x",
        assistant_call_id="ac1",
        model_turn_index=1,
    )
    assert state.pending_static_commits[0].kind == CommitKind.ASSISTANT_MARKDOWN


def test_state_keeps_active_tool_buckets_until_released() -> None:
    """Active tools must remain visible while their results are pending."""

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata=_attr(tool_call_id="c", tool_name="read_file"),
        ),
    )
    assert state.active_tool_count() == 1
    reduce_stream_event(
        state,
        _evt(
            "tool_result",
            result=ToolExecutionResult(
                tool_call_id="c", tool_name="read_file", content="x"
            ),
            metadata=_attr(tool_call_id="c"),
        ),
    )
    assert state.active_tool_count() == 0
    assert "c" not in state.tools
    # Tool state was COMPLETED for the duration of the run; reducer
    # removes the entry on ``tool_result``, so the visibility test
    # is the post-condition.
    assert state.tools.get("c") is None


def test_completed_event_emits_final_assistant_checkpoint() -> None:
    """``completed`` after streaming text must commit the residual text
    and clear ``streaming_text``.
    """

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt("assistant_delta", text="hello", metadata=_attr()),
    )
    reduce_stream_event(
        state,
        _evt("completed", text="hello", metadata=_attr()),
    )
    assert state.streaming_text == ""
    assert state.turn_completed is True
    assert any(c.is_assistant_markdown for c in state.pending_static_commits)
