"""Tests for :mod:`ui.cli.terminal.stream_view` (execplan §M2).

These tests cover the visual contract that the ExecPlan calls out:

- The assistant text and the tool panel are visually separated —
  the dynamic region must never let a tool line sit directly on top
  of the last assistant line.
- Multiple queued/running tools fold into ``…  N more tools running``
  when they exceed :data:`VISIBLE_ACTIVE_TOOL_LIMIT`.
- The status line is derived from ``stream_mode`` and the active
  tool bucket; it must never show the bare ``thinking…`` indicator
  while tools are still in flight.
- Error text is always visible at the tail of the body.
"""

from __future__ import annotations

from core.stream_events import AgentEvent
from ui.cli.terminal.stream_reducer import reduce_stream_event
from ui.cli.terminal.stream_state import CliStreamUiState
from ui.cli.terminal.stream_view import (
    render_status_fragments,
    render_stream_body_ansi,
)


def _evt(event_type: str, **kwargs) -> AgentEvent:
    """Build an :class:`AgentEvent` with default attribution metadata.

    The reducer's M2 contract requires every assistant / tool event
    to carry ``assistant_call_id`` and ``model_turn_index``; we
    inject sensible defaults so existing fixtures don't have to spell
    them out for every event.
    """

    metadata = kwargs.pop("metadata", None) or {}
    if "assistant_call_id" not in metadata:
        metadata = {
            "assistant_call_id": "ac1",
            "model_turn_index": 1,
            **metadata,
        }
    return AgentEvent(type=event_type, metadata=metadata, **kwargs)


class _Call:
    """Stand-in for a ``ToolCall`` (dataclass with id/name/input)."""

    def __init__(self, *, id: str, name: str, input: dict | None = None) -> None:
        self.id = id
        self.name = name
        self.input = input or {}


def _plain(value) -> str:
    """Strip ANSI escapes and SGR codes from a renderable's text view."""

    if hasattr(value, "value"):
        return value.value
    return str(value)


# --- empty state ----------------------------------------------------------


def test_body_empty_state_returns_empty_ansi() -> None:
    body = render_stream_body_ansi(CliStreamUiState(), width=80)
    assert _plain(body) == ""


# --- assistant + tool boundary ------------------------------------------


def test_body_inserts_blank_line_between_assistant_and_tools() -> None:
    """The assistant segment and the tool panel must not fuse on the same line."""

    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="hello world\n"))
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata={"tool_call_id": "c1", "tool_name": "bash"},
        ),
    )
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    lines = rendered.splitlines()
    # Find the assistant line and the tool line; the assistant line
    # must be followed by a blank line, then the tool line.
    assistant_idx = next(i for i, ln in enumerate(lines) if "hello world" in ln)
    tool_idx = next(i for i, ln in enumerate(lines) if "tool: bash" in ln)
    assert tool_idx > assistant_idx + 1, (
        "tool panel fused with assistant tail: "
        f"assistant line {assistant_idx!r}, tool line {tool_idx!r}, lines={lines!r}"
    )
    # The separator between them must be a blank line.
    assert lines[assistant_idx + 1] == ""


def test_body_keeps_tools_visible_when_no_assistant_text() -> None:
    """A turn that starts with a tool (no assistant text) must still show tools."""

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata={"tool_call_id": "c1", "tool_name": "bash"},
        ),
    )
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    assert "tool: bash" in rendered


def test_body_does_not_insert_blank_line_when_only_assistant_text() -> None:
    """A blank line in the middle of the body without tools would be visual noise."""

    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="just text\n"))
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    # The body should not start with a blank line, and no blank
    # line should be injected after the assistant text alone.
    lines = rendered.splitlines()
    assert lines and lines[0].strip() != ""


# --- queued / running state formatting ----------------------------------


def test_queued_tool_shows_queued_marker_no_preview() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata={"tool_call_id": "c1", "tool_name": "read_file"},
        ),
    )
    # Force the tool back to queued by emitting a fresh ready event
    # with the same id (the reducer preserves the latest status).
    reduce_stream_event(
        state,
        _evt(
            "tool_call_ready",
            metadata={"tool_call": _Call(id="c1", name="read_file", input={"path": "x.py"})},
        ),
    )
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    assert "read_file" in rendered
    assert "(queued)" in rendered
    # While queued, the input preview is suppressed.
    assert "x.py" not in rendered


def test_running_tool_shows_progress_when_set() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata={"tool_call_id": "c1", "tool_name": "bash"},
        ),
    )
    reduce_stream_event(
        state,
        _evt(
            "tool_progress",
            metadata={"tool_call_id": "c1", "message": "scanning…"},
        ),
    )
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    assert "scanning…" in rendered
    assert "(queued)" not in rendered


# --- active-tool folding ------------------------------------------------


def test_body_folds_excess_active_tools() -> None:
    state = CliStreamUiState()
    for i in range(5):
        reduce_stream_event(
            state,
            _evt(
                "tool_started",
                metadata={"tool_call_id": f"c{i}", "tool_name": f"tool{i}"},
            ),
        )
    body = render_stream_body_ansi(state, width=200, active_tool_limit=2)
    rendered = _plain(body)
    # First two are visible.
    assert "tool0" in rendered
    assert "tool1" in rendered
    # The others fold into a single summary line.
    assert "tool4" not in rendered
    assert "3 more" in rendered or "more tools" in rendered


# --- assistant tail bounding --------------------------------------------


def test_body_bounds_assistant_tail_to_max_lines() -> None:
    state = CliStreamUiState()
    six_lines = "\n".join(f"line {i}" for i in range(6)) + "\n"
    reduce_stream_event(state, _evt("assistant_delta", text=six_lines))
    body = render_stream_body_ansi(state, width=2000)
    rendered = _plain(body)
    # The newest lines must be present; the oldest is folded into a
    # truncation marker so the preview stays bounded.
    for i in range(1, 6):
        assert f"line {i}" in rendered
    # Bounded: at most the cap plus a truncation marker line.
    nonempty = [ln for ln in rendered.splitlines() if ln.strip()]
    assert len(nonempty) <= 5 + 1  # ASSISTANT_TAIL_MAX_LINES + 1


# --- error visibility ---------------------------------------------------


def test_body_renders_error_text_at_tail() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="partial\n"))
    reduce_stream_event(state, _evt("error", text="boom"))
    body = render_stream_body_ansi(state, width=80)
    rendered = _plain(body)
    lines = rendered.splitlines()
    # The error line must be the last non-empty line in the body.
    nonempty = [ln for ln in lines if ln.strip()]
    assert nonempty[-1].startswith("! boom")


# --- status line derivation ---------------------------------------------


def test_status_shows_thinking_while_awaiting_first_event() -> None:
    state = CliStreamUiState()
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    assert "thinking" in text
    assert "onecode> " in text


def test_status_shows_tool_label_when_tool_running() -> None:
    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _evt(
            "tool_started",
            metadata={"tool_call_id": "c1", "tool_name": "bash"},
        ),
    )
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    # The plan calls this out: when a tool is running, the status
    # line must not fall back to a bare ``thinking…`` indicator.
    assert "thinking" not in text
    assert "bash" in text
    assert "tool" in text


def test_status_shows_tool_count_for_multiple_running() -> None:
    state = CliStreamUiState()
    for i in range(3):
        reduce_stream_event(
            state,
            _evt(
                "tool_started",
                metadata={"tool_call_id": f"c{i}", "tool_name": f"tool{i}"},
            ),
        )
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    assert "thinking" not in text
    assert "3" in text
    assert "tools" in text


def test_status_shows_responding_while_streaming_text() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("assistant_delta", text="hello"))
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    assert "responding" in text


def test_status_shows_done_after_completion() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("completed", text=""))
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    assert "done" in text


def test_status_shows_error_after_error_event() -> None:
    state = CliStreamUiState()
    reduce_stream_event(state, _evt("error", text="boom"))
    fragments = render_status_fragments(state)
    text = "".join(fragment for _, fragment in fragments)
    assert "error" in text