"""Tests for the streaming event coalescer.

The coalescer folds bursts of high-frequency events
(``assistant_delta`` / ``tool_progress`` / ``tool_call_delta``) into
a single reducer pass within a 16 ms window. Low-frequency events
apply immediately and force a flush.

These tests verify the four invariants the production code depends
on:

1. High-frequency events in a window are merged into a single
   ``apply`` call.
2. A low-frequency event flushes any pending batch before applying.
3. ``flush()`` emits every pending event with no loss.
4. ``tool_progress`` events for the same call id collapse to the
   latest value.
5. ``should_flush`` flips on the window boundary (using an injected
   fake clock).
"""

from __future__ import annotations

from typing import Any

from core.stream_events import AgentEvent
from ui.cli.terminal.streaming_coalescer import StreamingCoalescer


def _delta(text: str) -> AgentEvent:
    return AgentEvent(type="assistant_delta", text=text)


def _progress(call_id: str, message: str) -> AgentEvent:
    return AgentEvent(
        type="tool_progress",
        metadata={"tool_call_id": call_id, "message": message},
    )


def _tool_name(name: str) -> AgentEvent:
    return AgentEvent(type="tool_call_delta", metadata={"name": name})


def _result(call_id: str = "c1") -> AgentEvent:
    from services.tools.types import ToolExecutionResult
    return AgentEvent(
        type="tool_result",
        result=ToolExecutionResult(
            tool_call_id=call_id,
            tool_name="bash",
            content="ok",
        ),
    )


def test_assistant_deltas_are_coalesced() -> None:
    """A burst of deltas within the window must be folded into one apply call."""

    applied: list[AgentEvent] = []
    coalescer = StreamingCoalescer(
        apply=applied.append,
        window_seconds=0.016,
        clock=lambda: 0.0,  # never advance; only manual flushes
    )
    for _ in range(50):
        pushed_low_freq = coalescer.push(_delta("a"))
        assert pushed_low_freq is False
    # No apply calls have happened yet.
    assert applied == []
    # A flush must fold the entire burst into a single event.
    coalescer.flush()
    assert len(applied) == 1
    assert applied[0].type == "assistant_delta"
    assert applied[0].text == "a" * 50


def test_low_freq_event_flushes_pending_batch() -> None:
    """A ``tool_result`` event must flush the pending batch first, then apply."""

    applied: list[AgentEvent] = []
    coalescer = StreamingCoalescer(
        apply=applied.append,
        window_seconds=0.016,
        clock=lambda: 0.0,
    )
    for _ in range(20):
        coalescer.push(_delta("x"))
    # The first ``tool_result`` is a low-frequency event. It should
    # flush the deltas and then apply itself.
    coalescer.push(_result())
    # Order matters: the merged delta comes first, the result second.
    assert [ev.type for ev in applied] == ["assistant_delta", "tool_result"]
    assert applied[0].text == "x" * 20
    assert applied[1].type == "tool_result"
    assert applied[1].result is not None


def test_flush_emits_every_pending_event() -> None:
    """Calling ``flush()`` twice in a row must not double-emit."""

    applied: list[AgentEvent] = []
    coalescer = StreamingCoalescer(
        apply=applied.append,
        window_seconds=0.016,
        clock=lambda: 0.0,
    )
    for ch in "abcde":
        coalescer.push(_delta(ch))
    coalescer.flush()
    coalescer.flush()  # no-op: nothing pending
    assert len(applied) == 1
    assert applied[0].text == "abcde"


def test_tool_progress_collapses_to_latest_message() -> None:
    """Progress messages for the same call id collapse to the latest value."""

    applied: list[AgentEvent] = []
    coalescer = StreamingCoalescer(
        apply=applied.append,
        window_seconds=0.016,
        clock=lambda: 0.0,
    )
    for i in range(10):
        coalescer.push(_progress("c1", f"step {i}"))
    # Mix in a progress for a different call id to make sure that
    # does not interfere.
    coalescer.push(_progress("c2", "other"))
    coalescer.flush()
    # Two apply calls: one for c1 (with the latest message), one for
    # c2. c1's intermediate messages must be dropped.
    progress_events = [ev for ev in applied if ev.type == "tool_progress"]
    assert len(progress_events) == 2
    by_call_id = {ev.metadata["tool_call_id"]: ev.metadata["message"] for ev in progress_events}
    assert by_call_id == {"c1": "step 9", "c2": "other"}


def test_should_flush_respects_window_with_injected_clock() -> None:
    """``should_flush`` returns True only after the window has elapsed."""

    clock_values = iter([0.0, 0.005, 0.020])
    coalescer = StreamingCoalescer(
        apply=lambda ev: None,
        window_seconds=0.016,
        clock=lambda: next(clock_values),
    )
    # At t=0 with no pending events, ``should_flush`` is False.
    assert coalescer.should_flush(0.0) is False
    coalescer.push(_delta("a"))
    # At t=0.005 (5 ms) the window has not elapsed.
    assert coalescer.should_flush(0.005) is False
    # At t=0.020 the window has elapsed — should flush.
    assert coalescer.should_flush(0.020) is True


def test_should_flush_false_after_recent_manual_flush() -> None:
    """A manual ``flush()`` resets the window clock."""

    clock_values = iter([0.0, 0.001, 0.005, 0.020])
    coalescer = StreamingCoalescer(
        apply=lambda ev: None,
        window_seconds=0.016,
        clock=lambda: next(clock_values),
    )
    coalescer.push(_delta("a"))
    coalescer.flush()  # clock is at 0.001
    # Immediately after, a new delta is pushed.
    coalescer.push(_delta("b"))
    # At t=0.005 (4 ms after the last flush) the window has not
    # elapsed.
    assert coalescer.should_flush(0.005) is False
    # At t=0.020 (19 ms after the last flush) the window has elapsed.
    assert coalescer.should_flush(0.020) is True


def test_tool_call_delta_keeps_first_name() -> None:
    """``tool_call_delta`` only records the first name in a window."""

    applied: list[AgentEvent] = []
    coalescer = StreamingCoalescer(
        apply=applied.append,
        window_seconds=0.016,
        clock=lambda: 0.0,
    )
    coalescer.push(_tool_name("bash"))
    coalescer.push(_tool_name("grep"))
    coalescer.push(_tool_name("glob"))
    coalescer.flush()
    tool_call_events = [ev for ev in applied if ev.type == "tool_call_delta"]
    assert len(tool_call_events) == 1
    assert tool_call_events[0].metadata["name"] == "bash"


def test_push_returns_true_only_for_low_freq_events() -> None:
    """High-frequency events return False; low-frequency events return True."""

    coalescer = StreamingCoalescer(
        apply=lambda ev: None,
        window_seconds=0.016,
        clock=lambda: 0.0,
    )
    assert coalescer.push(_delta("a")) is False
    assert coalescer.push(_progress("c", "x")) is False
    assert coalescer.push(_tool_name("bash")) is False
    # A ``tool_result`` is a low-frequency event.
    assert coalescer.push(_result()) is True
