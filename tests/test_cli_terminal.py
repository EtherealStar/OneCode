"""Tests for the inline terminal REPL (``ui/cli/terminal``).

These tests grow with the milestones in
``docs/exec-plans/active/cli-inline-terminal-ui-refactor-execplan.md``:

- M2: static-region printers (reverse user line, ``onecode>`` prefix,
  tool banners).
- M3: completion adapter + Enter/Tab semantics + input queue.
- M5: alternate-screen (DEC 1049) lifecycle.

The tests deliberately avoid spinning up a real terminal — they
capture a Rich console bound to an ``io.StringIO`` and assert on the
exported text, or drive the pure-Python adapters directly.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console
from rich.text import Text

from ui.cli.terminal import static_output as so
from ui.cli.terminal import repl as repl_module
from ui.cli.terminal import transient
from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.repl import InlineRepl
from ui.cli.terminal.prompt_session import (
    PromptSession,
    PromptSubmission,
    SubmissionKind,
    strip_osc11_reply_fragments,
)
from ui.cli.terminal.queue import InputQueue, QueuedInput
from ui.cli.theme import RICH_THEME
from ui.cli.types import CommandResult

from test_cli_commands import make_runtime


class _FakeRuntime:
    """Minimal stand-in for CliRuntime in completion tests.

    ``suggestions_for`` only reads ``workspace`` for file/directory
    completion; command and session completion ignore it.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace


@pytest.fixture
def captured_console() -> io.StringIO:
    """Bind the module-level static console to a captured buffer."""

    buffer = io.StringIO()
    so.reset_static_console()
    # force_terminal + an explicit color system so Rich emits SGR
    # sequences even though StringIO isn't a real TTY (auto-detection
    # would otherwise strip color in the test environment).
    so._STATIC_CONSOLE = Console(  # noqa: SLF001
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=80,
        theme=RICH_THEME,
    )
    yield buffer
    so.reset_static_console()


# --- M2: static output ----------------------------------------------------


def test_user_submitted_uses_reverse_style_dark(captured_console: io.StringIO) -> None:
    so.print_user_submitted("hello", brightness="dark")
    output = captured_console.getvalue()
    assert "> hello" in output
    # white-on-black reverse: Rich emits a SGR sequence for the style.
    assert "\x1b[" in output


def test_user_reverse_style_switches_with_brightness() -> None:
    assert so.user_reverse_style("dark") == "white on black"
    assert so.user_reverse_style("light") == "black on white"


def test_restored_user_messages_use_explicit_reverse_style(
    captured_console: io.StringIO,
) -> None:
    from ui.cli.terminal.transcript_replay import replay_messages_to_static

    replay_messages_to_static(
        [{"role": "user", "content": "hello"}],
        brightness="light",
    )

    output = captured_console.getvalue()
    assert "> hello" in output
    # Reuses the live reverse-video user line, so a SGR sequence is emitted.
    assert "\x1b[" in output


def test_replay_messages_reuse_normal_static_renderers(
    captured_console: io.StringIO,
    tmp_path: Path,
) -> None:
    from services.tools.types import ToolExecutionResult
    from ui.cli.terminal import static_output as so
    from ui.cli.terminal.transcript_replay import replay_messages_to_static

    messages = [
        {"role": "user", "content": "restore this"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_read", "function": {"name": "read_file"}}],
        },
        {
            "role": "tool_result",
            "tool_call_id": "call_read",
            "tool_name": "glob",
            "content": "content",
            "is_error": False,
            "metadata": {"num_files": 10, "total_matches_before_pagination": 31},
        },
        {"role": "assistant", "content": "restored answer"},
    ]

    replay_messages_to_static(messages, brightness="dark", workspace=tmp_path)
    replayed = captured_console.getvalue()

    # User line is reverse-video and present.
    assert "> restore this" in replayed
    # Assistant text reply went through the Markdown commit path.
    assert "onecode>" in replayed
    assert "restored answer" in replayed
    # Tool-only assistant message produced no synthetic "assistant: <tool call>".
    assert "<tool call" not in replayed
    # Tool result went through the normal print_tool_result container.
    assert "⎿" in replayed
    assert "[glob] Found 31 files, showing 10" in replayed


def test_assistant_prefix_is_printed(captured_console: io.StringIO) -> None:
    so.print_assistant_start()
    output = captured_console.getvalue()
    assert "onecode>" in output


def test_assistant_markdown_renders_body(captured_console: io.StringIO) -> None:
    so.print_assistant_markdown("plain reply")
    output = captured_console.getvalue()
    assert "plain reply" in output


def test_assistant_markdown_strips_rich_line_padding(
    captured_console: io.StringIO,
) -> None:
    so.print_assistant_markdown("plain reply")
    output = captured_console.getvalue()
    body_lines = [
        line for line in output.splitlines() if "plain reply" in line
    ]

    assert body_lines == ["plain reply"]


def test_assistant_markdown_renders_table_header_style(
    captured_console: io.StringIO,
) -> None:
    so.print_assistant_markdown("| name | value |\n| --- | --- |\n| a | 1 |")
    output = captured_console.getvalue()
    assert "name" in output
    assert "value" in output


def test_assistant_markdown_skips_empty(captured_console: io.StringIO) -> None:
    so.print_assistant_markdown("")
    assert captured_console.getvalue() == ""


def test_tool_banner_start_shows_name_and_args(captured_console: io.StringIO) -> None:
    so.print_tool_banner_start("read_file", "call_1", {"path": "foo.py", "offset": 10})
    output = captured_console.getvalue()
    assert "read_file" in output
    assert "call_1" in output
    assert "foo.py" in output


def test_tool_banner_truncates_long_arguments(captured_console: io.StringIO) -> None:
    long_value = "x" * 500
    so.print_tool_banner_start("bash", "call_2", {"command": long_value})
    output = captured_console.getvalue()
    # The argument preview must be bounded; the raw 500-char value
    # should never appear verbatim.
    assert long_value not in output
    assert "…" in output


def test_untrusted_mcp_notice(captured_console: io.StringIO) -> None:
    so.print_untrusted_mcp_notice("server-x", "node server.js")
    output = captured_console.getvalue()
    assert "server-x" in output
    assert "node server.js" in output
    assert "Skipped untrusted MCP server" in output


# --- M3: input queue ------------------------------------------------------


def test_queue_is_fifo() -> None:
    queue = InputQueue()
    queue.push("first")
    queue.push("second")
    assert len(queue) == 2
    first = queue.pop()
    second = queue.pop()
    assert first is not None and second is not None
    assert first.text == "first"
    assert second.text == "second"
    assert queue.pop() is None


def test_queue_skips_blank_lines() -> None:
    queue = InputQueue()
    assert queue.push("   ") is None
    assert queue.push("") is None
    assert len(queue) == 0


def test_queue_snapshot_is_readonly_copy() -> None:
    queue = InputQueue()
    queue.push("a")
    snapshot = queue.snapshot()
    queue.push("b")
    # snapshot was taken before "b" was pushed.
    assert len(snapshot) == 1
    assert snapshot[0].text == "a"


def test_queue_classifies_slash_vs_prompt() -> None:
    queue = InputQueue()
    prompt_item = queue.push("hello world")
    slash_item = queue.push("/status")
    indented_slash = queue.push("  /clear  ")
    assert prompt_item is not None and prompt_item.kind == "prompt"
    assert slash_item is not None and slash_item.kind == "slash"
    # Leading whitespace before the "/" is tolerated for kind detection.
    assert indented_slash is not None and indented_slash.kind == "slash"
    # The text keeps the leading slash and the content the user typed.
    assert slash_item.text == "/status"


def test_queue_assigns_monotonic_sequence() -> None:
    queue = InputQueue()
    first = queue.push("one")
    second = queue.push("two")
    third = queue.push("/help")
    assert first is not None and second is not None and third is not None
    assert (first.sequence, second.sequence, third.sequence) == (0, 1, 2)


def test_queue_snapshot_returns_typed_records() -> None:
    queue = InputQueue()
    queue.push("hello")
    queue.push("/status")
    snapshot = queue.snapshot()
    assert all(isinstance(item, QueuedInput) for item in snapshot)
    assert [item.kind for item in snapshot] == ["prompt", "slash"]


def test_queue_clear_drops_everything() -> None:
    queue = InputQueue()
    queue.push("hello")
    queue.push("/status")
    queue.clear()
    assert len(queue) == 0
    assert queue.pop() is None


# --- M5: alternate-screen lifecycle ---------------------------------------


class _FakeTtyStream:
    """A StringIO that claims to be a TTY so DEC 1049 is emitted."""

    def __init__(self) -> None:
        self._buffer = io.StringIO()

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return self._buffer.write(text)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        return self._buffer.getvalue()


class _FakeTtyForBrightness:
    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        raise AssertionError("OSC 11 should not be probed")


def test_alternate_screen_emits_dec_1049() -> None:
    transient.reset_for_tests()
    stream = _FakeTtyStream()
    transient.enter_alternate_screen(stream)
    assert transient.is_alternate_screen_active()
    transient.exit_alternate_screen(stream)
    assert not transient.is_alternate_screen_active()
    output = stream.getvalue()
    assert "\x1b[?1049h" in output
    assert "\x1b[?1049l" in output


def test_alternate_screen_noop_on_non_tty() -> None:
    transient.reset_for_tests()
    buffer = io.StringIO()  # not a TTY
    transient.enter_alternate_screen(buffer)
    assert not transient.is_alternate_screen_active()
    assert buffer.getvalue() == ""


def test_transient_scope_exits_on_exception() -> None:
    transient.reset_for_tests()
    stream = _FakeTtyStream()
    with pytest.raises(RuntimeError):
        with transient.transient_terminal_scope(stream):
            assert transient.is_alternate_screen_active()
            raise RuntimeError("boom")
    # The scope must restore the primary buffer even on exception.
    assert not transient.is_alternate_screen_active()
    assert "\x1b[?1049l" in stream.getvalue()


def test_terminal_brightness_skips_osc11_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from ui.cli.terminal import detect

    monkeypatch.setattr(detect.platform, "system", lambda: "Windows")
    monkeypatch.setenv("COLORFGBG", "0;15")

    assert detect.detect_terminal_brightness(_FakeTtyForBrightness()) == "light"


def test_osc11_reply_parser_classifies_light_and_dark() -> None:
    from ui.cli.terminal.detect import _brightness_from_osc11_reply

    assert _brightness_from_osc11_reply(b"\x1b]11;rgb:f8f8/f8f8/f8f8\x07") == "light"
    assert _brightness_from_osc11_reply(b"\x1b]11;rgb:0000/0000/0000\x1b\\") == "dark"


# --- M3: completion adapter -----------------------------------------------


def test_completer_yields_slash_commands(tmp_path: Path) -> None:
    completer = InlineCompleter(_FakeRuntime(tmp_path))
    document = Document("/st", cursor_position=3)
    completions = list(completer.get_completions(document, CompleteEvent()))
    texts = [completion.text for completion in completions]
    assert "/status" in texts
    # The start_position must delete the typed "/st" so the
    # replacement does not duplicate the prefix.
    status = next(c for c in completions if c.text == "/status")
    assert status.start_position == -3


def test_completer_empty_without_slash(tmp_path: Path) -> None:
    completer = InlineCompleter(_FakeRuntime(tmp_path))
    document = Document("hello", cursor_position=5)
    completions = list(completer.get_completions(document, CompleteEvent()))
    assert completions == []


def test_completer_none_runtime_is_safe() -> None:
    completer = InlineCompleter(None)
    document = Document("/st", cursor_position=3)
    assert list(completer.get_completions(document, CompleteEvent())) == []


# --- M3: Enter / Tab semantics --------------------------------------------


def _drive_prompt(
    session: PromptSession,
    keys: str,
) -> PromptSubmission:
    """Feed ``keys`` into a PromptSession via a pipe input."""

    async def run() -> PromptSubmission:
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            return await session.read(
                input=pipe,
                output=DummyOutput(),
            )

    return asyncio.run(run())


def test_enter_with_open_menu_accepts_and_submits(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    # Type "/st", then Enter. The menu auto-selects the first item
    # (/status) and Enter submits it.
    submission = _drive_prompt(session, "/st\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "/status"


def test_file_completion_appends_space_for_continued_editing(tmp_path: Path) -> None:
    from prompt_toolkit.buffer import Buffer

    from ui.cli.terminal.completer import InlineCompleter
    from ui.cli.terminal.prompt_session import (
        _apply_completion_for_edit,
        _highlighted_completion,
    )

    (tmp_path / "ui" / "cli").mkdir(parents=True)
    (tmp_path / "ui" / "cli" / "renderer.py").write_text("", encoding="utf-8")
    buffer = Buffer(
        completer=InlineCompleter(_FakeRuntime(tmp_path)),
        complete_while_typing=False,
        multiline=False,
    )
    buffer.text = "@ui/cli/ren"
    buffer.cursor_position = len(buffer.text)

    completion = _highlighted_completion(buffer)
    assert completion is not None
    _apply_completion_for_edit(buffer, completion)

    assert buffer.text == "@ui/cli/renderer.py "


def test_enter_completes_directory_mention_without_submitting(
    tmp_path: Path,
) -> None:
    from prompt_toolkit.buffer import Buffer

    from ui.cli.terminal.prompt_session import _directory_mention_token_end

    (tmp_path / "docs" / "reference").mkdir(parents=True)
    buffer = Buffer(
        completer=InlineCompleter(_FakeRuntime(tmp_path)),
        complete_while_typing=False,
        multiline=False,
    )
    buffer.text = "@docs/"
    buffer.cursor_position = len(buffer.text)

    token_end = _directory_mention_token_end(buffer)
    assert token_end == len(buffer.text)
    buffer.cursor_position = token_end
    buffer.insert_text(" ")

    assert buffer.text == "@docs/ "
    assert list(buffer.completer.get_completions(buffer.document, CompleteEvent())) == []


def test_tab_fills_without_submitting_then_enter_submits(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    # "/st" + Tab fills the box with "/status" but does NOT submit;
    # the following Enter then submits the filled text.
    submission = _drive_prompt(session, "/st\t\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "/status"


def test_suggestion_panel_fragments_include_command_descriptions(tmp_path: Path) -> None:
    from prompt_toolkit.buffer import Buffer

    from ui.cli.terminal.completer import InlineCompleter
    from ui.cli.terminal.prompt_session import _suggestion_fragments

    buffer = Buffer(
        completer=InlineCompleter(_FakeRuntime(tmp_path)),
        complete_while_typing=True,
        multiline=False,
    )
    buffer.text = "/st"
    buffer.cursor_position = len(buffer.text)
    text = "".join(fragment for _, fragment in _suggestion_fragments(buffer))

    assert "/status" in text
    assert "Show runtime status" in text


def test_prompt_hint_is_hidden_when_idle(tmp_path: Path) -> None:
    from prompt_toolkit.buffer import Buffer

    from ui.cli.terminal.completer import InlineCompleter
    from ui.cli.terminal.prompt_session import _PromptHint, _hint_text

    buffer = Buffer(
        completer=InlineCompleter(_FakeRuntime(tmp_path)),
        complete_while_typing=True,
        multiline=False,
    )

    assert _hint_text(buffer, _PromptHint("").text) == ""


def test_enter_plain_text_submits_literally(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    submission = _drive_prompt(session, "hello world\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "hello world"


def test_ctrl_d_on_empty_buffer_exits(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    submission = _drive_prompt(session, "\x04")  # Ctrl-D
    assert submission.kind is SubmissionKind.EXIT


def test_idle_ctrl_c_once_clears_input_and_keeps_prompt(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    submission = _drive_prompt(session, "abc\x03done\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "done"


def test_idle_ctrl_c_twice_exits(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    submission = _drive_prompt(session, "\x03\x03")
    assert submission.kind is SubmissionKind.EXIT


def test_idle_ctrl_c_second_press_after_window_does_not_exit(tmp_path: Path) -> None:
    values = iter((0.0, 2.0))
    session = PromptSession(
        _FakeRuntime(tmp_path),
        InputQueue(),
        exit_confirm_window_seconds=1.5,
        clock=lambda: next(values),
    )
    submission = _drive_prompt(session, "\x03\x03hello\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "hello"


def test_strip_osc11_reply_fragments_is_narrow() -> None:
    assert strip_osc11_reply_fragments("]11;rgb:f8f8/f8f8/f8f8\\") == ""
    assert strip_osc11_reply_fragments("keep ]12;rgb:f8f8/f8f8/f8f8\\") == "keep ]12;rgb:f8f8/f8f8/f8f8\\"


# --- command handling -----------------------------------------------------


def test_handle_command_reset_main_view_rebuilds_prompt_and_prints_banner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    new_runtime = make_runtime(tmp_path / "new")
    repl = InlineRepl(runtime)
    old_prompt = repl._prompt
    buffer = io.StringIO()
    repl._console = Console(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=80,
        theme=RICH_THEME,
    )
    monkeypatch.setattr(repl, "_terminal_height", lambda: 5)

    def fake_dispatch(runtime_arg, line: str) -> CommandResult:
        assert runtime_arg is runtime
        assert line == "/clear"
        return CommandResult(
            runtime=new_runtime,
            reset_main_view=True,
            renderable=Text("clear notice"),
        )

    monkeypatch.setattr(repl_module, "dispatch_command", fake_dispatch)

    asyncio.run(repl._handle_command("/clear"))

    output = buffer.getvalue()
    assert repl._runtime is new_runtime
    assert repl._prompt is not old_prompt
    assert repl._prompt._runtime is new_runtime
    assert output.startswith("\n" * 6)
    assert "OneCode" in output
    assert "clear notice" in output


def test_connect_success_resets_main_view_with_new_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = make_runtime(tmp_path)
    new_runtime = make_runtime(tmp_path / "new")
    new_runtime = replace(
        new_runtime,
        provider_label="DeepSeek",
        model="deepseek-chat",
    )
    repl = InlineRepl(runtime)
    old_prompt = repl._prompt
    buffer = io.StringIO()
    repl._console = Console(
        file=buffer,
        force_terminal=True,
        color_system="standard",
        width=80,
        theme=RICH_THEME,
    )
    monkeypatch.setattr(repl, "_terminal_height", lambda: 2)

    async def fake_run_connect_flow(runtime_arg):
        assert runtime_arg is runtime
        return SimpleNamespace(
            cancelled=False,
            runtime=new_runtime,
            renderable=Text("已连接到 DeepSeek (deepseek-chat)。"),
        )

    monkeypatch.setattr(repl_module, "run_connect_flow", fake_run_connect_flow)

    asyncio.run(repl._handle_command("/connect"))

    output = buffer.getvalue()
    assert repl._runtime is new_runtime
    assert repl._prompt is not old_prompt
    assert "deepseek-chat" in output
    assert "已连接到 DeepSeek" in output


# --- M4: streaming session ------------------------------------------------


def _agent_event(event_type: str, **kwargs):
    from core.stream_events import AgentEvent

    metadata = kwargs.pop("metadata", None)
    if metadata is None:
        # Default to a stable attribution payload so reducer-side
        # guards don't trip on the legacy test fixtures.
        metadata = {"assistant_call_id": "ac1", "model_turn_index": 1}
    return AgentEvent(type=event_type, metadata=metadata, **kwargs)


def test_reducer_accumulates_deltas() -> None:
    """The pure reducer folds deltas into ``state.streaming_text``."""

    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState

    state = CliStreamUiState()
    reduce_stream_event(state, _agent_event("assistant_delta", text="Hello "))
    reduce_stream_event(state, _agent_event("assistant_delta", text="world"))
    assert state.streaming_text == "Hello world"


def test_reducer_completed_fallback_text() -> None:
    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState

    state = CliStreamUiState()
    # No deltas, only a completed event carrying the full text.
    # The reducer stages an ``assistant_markdown`` commit and clears
    # the dynamic ``streaming_text``.
    reduce_stream_event(state, _agent_event("completed", text="final answer"))
    assert state.streaming_text == ""
    assert any(c.is_assistant_markdown for c in state.pending_static_commits)


def test_reducer_assistant_message_completed_emits_checkpoint() -> None:
    """``assistant_message_completed`` clears ``streaming_text`` and
    emits an assistant checkpoint for the accumulated text.
    """

    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState

    state = CliStreamUiState()
    # First a delta, then the completion event with matching text.
    reduce_stream_event(
        state,
        _agent_event("assistant_delta", text="final answer"),
    )
    reduce_stream_event(
        state, _agent_event("assistant_message_completed", text="final answer")
    )
    # The reducer clears the dynamic region and stages a checkpoint.
    assert state.streaming_text == ""
    assert any(c.is_assistant_markdown for c in state.pending_static_commits)


def test_reducer_tool_call_ready_updates_state_without_static_banner(
    captured_console: io.StringIO,
) -> None:
    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState

    class _Call:
        id = "call_9"
        name = "grep"
        input = {"pattern": "TODO"}

    state = CliStreamUiState()
    reduce_stream_event(
        state,
        _agent_event(
            "tool_call_ready",
            metadata={
                "assistant_call_id": "ac1",
                "model_turn_index": 1,
                "tool_call": _Call(),
            },
        ),
    )
    assert captured_console.getvalue() == ""
    assert state.tools["call_9"].tool_name == "grep"
    assert state.tools["call_9"].status == "queued"


def test_streaming_session_prints_only_tool_result_summary(
    captured_console: io.StringIO,
    tmp_path: Path,
) -> None:
    from services.tools.types import ToolExecutionResult
    from ui.cli.terminal.stream_session import StreamingSession
    from ui.cli.terminal.stream_state import (
        StreamingToolUseState,
        ToolStatus,
    )

    result = ToolExecutionResult(
        tool_call_id="call_9",
        tool_name="glob",
        content="content",
        metadata={"num_files": 10, "total_matches_before_pagination": 31},
    )
    # Use a real StreamingSession so the flush step that prints the
    # tool result to the static region runs.
    session = StreamingSession(workspace=tmp_path)
    # Pre-seed the active tool list; the reducer will move it to
    # ``pending_static_commits`` when the ``tool_result`` event arrives.
    session.state.tools["call_9"] = StreamingToolUseState(
        call_id="call_9",
        tool_name="glob",
        status=ToolStatus.RUNNING,
    )

    session._apply_event(
        _agent_event(
            "tool_result",
            result=result,
            metadata={
                "assistant_call_id": "ac1",
                "model_turn_index": 1,
                "tool_call_id": "call_9",
            },
        )
    )
    session._commit_pending_to_coordinator()
    asyncio.run(session.coordinator.flush_ready_checkpoints())

    output = captured_console.getvalue()
    assert "[glob] Found 31 files, showing 10" in output
    assert "● glob" not in output


def test_view_handles_partial_code_fence() -> None:
    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState
    from ui.cli.terminal.stream_view import render_stream_body_ansi

    state = CliStreamUiState()
    reduce_stream_event(
        state, _agent_event("assistant_delta", text="```python\nprint('hi')\n")
    )  # unbalanced fence
    ansi = render_stream_body_ansi(state, width=60)
    # Must not raise and must include the code text without a synthetic
    # closing fence that the Markdown renderer would add.
    rendered = ansi.value if hasattr(ansi, "value") else str(ansi)
    assert "print" in rendered


def test_view_bounds_assistant_tail_height() -> None:
    from ui.cli.terminal.stream_reducer import reduce_stream_event
    from ui.cli.terminal.stream_state import CliStreamUiState
    from ui.cli.terminal.stream_view import (
        ASSISTANT_TAIL_MAX_LINES,
        render_stream_body_ansi,
    )

    many_lines = "\n\n".join(f"line {i}" for i in range(100))
    state = CliStreamUiState()
    reduce_stream_event(state, _agent_event("assistant_delta", text=many_lines))
    ansi = render_stream_body_ansi(state, width=60)
    rendered = ansi.value if hasattr(ansi, "value") else str(ansi)
    # Bounded to the preview window plus the truncation marker.
    assert rendered.count("\n") <= ASSISTANT_TAIL_MAX_LINES + 1


def test_streaming_session_drains_and_commits(captured_console: io.StringIO) -> None:
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="part one ")
        yield _agent_event("assistant_delta", text="part two")
        # Use a ``completed`` event without prior ``assistant_message_completed``;
        # the reducer commits the accumulated streaming text.
        yield _agent_event("completed", text="")

    async def run() -> None:
        session = StreamingSession()
        with create_pipe_input() as pipe:
            state = await session.run(
                events(),
                input=pipe,
                output=DummyOutput(),
            )
        # After the checkpoint commit, ``streaming_text`` is cleared.
        assert state.streaming_text == ""
        assert not session.cancelled

    asyncio.run(run())
    # The accumulated text is committed to the static region as Markdown.
    assert "part one part two" in captured_console.getvalue()


def test_streaming_session_commits_on_assistant_message_completed(
    captured_console: io.StringIO,
) -> None:
    from ui.cli.terminal.stream_session import StreamingSession

    reached_post_completion_cleanup = asyncio.Event()

    async def events():
        yield _agent_event("assistant_delta", text="final text")
        # ``assistant_message_completed`` triggers an immediate
        # checkpoint commit in the live dynamic region, not at turn end.
        yield _agent_event("assistant_message_completed", text="final text")
        await asyncio.sleep(0.05)
        reached_post_completion_cleanup.set()
        yield _agent_event("completed", text="final text")

    async def run() -> tuple[bool, str]:
        session = StreamingSession()
        with create_pipe_input() as pipe:
            await session.run(
                events(),
                input=pipe,
                output=DummyOutput(),
            )
        return reached_post_completion_cleanup.is_set(), captured_console.getvalue()

    cleanup_ran, output = asyncio.run(run())
    assert cleanup_ran
    # Exactly one assistant_markdown commit was emitted for the
    # final text; the ``completed`` event at the end does not
    # re-commit the same text.
    assert output.count("final text") == 1


def test_streaming_session_cancels_on_escape(captured_console: io.StringIO) -> None:
    from ui.cli.terminal.stream_session import StreamingSession

    async def slow_events():
        # Emit one delta then stall, giving the Esc key time to fire.
        yield _agent_event("assistant_delta", text="working…")
        while True:
            await asyncio.sleep(0.05)
            yield _agent_event("assistant_delta", text=".")

    async def run() -> bool:
        session = StreamingSession()
        with create_pipe_input() as pipe:
            pipe.send_text("\x1b")  # Esc
            await session.run(
                slow_events(),
                input=pipe,
                output=DummyOutput(),
            )
        return session.cancelled

    cancelled = asyncio.run(run())
    assert cancelled
    assert "已取消" in captured_console.getvalue()


def test_streaming_session_cancels_on_ctrl_c(captured_console: io.StringIO) -> None:
    from ui.cli.terminal.stream_session import StreamingSession

    async def slow_events():
        yield _agent_event("assistant_delta", text="working…")
        while True:
            await asyncio.sleep(0.05)
            yield _agent_event("assistant_delta", text=".")

    async def run() -> bool:
        session = StreamingSession()
        with create_pipe_input() as pipe:
            pipe.send_text("\x03")  # Ctrl-C
            await session.run(
                slow_events(),
                input=pipe,
                output=DummyOutput(),
            )
        return session.cancelled

    cancelled = asyncio.run(run())
    assert cancelled
    assert "已取消" in captured_console.getvalue()


# --- running-turn input box (execplan §M2) ---------------------------------


def test_streaming_session_with_queue_enqueues_input_and_does_not_exit(
    captured_console: io.StringIO,
) -> None:
    """Enter inside the running input box pushes the line onto the queue
    and the session keeps running until the agent stream ends.

    The pipe sends "second turn\\r" mid-turn. The session must:
      1. NOT exit on the Enter keypress (it only exits on completed/error).
      2. Push the typed text onto the shared InputQueue.
      3. Clear the running buffer so the next keypress starts fresh.
    """

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="working…")
        # Stall so the pipe keypress has time to land.
        await asyncio.sleep(0.05)
        yield _agent_event("assistant_delta", text=".")
        yield _agent_event("completed", text="")

    async def run() -> tuple[int, str]:
        queue = InputQueue()
        session = StreamingSession(queue=queue)
        with create_pipe_input() as pipe:
            pipe.send_text("second turn\r")
            await session.run(events(), input=pipe, output=DummyOutput())
        snapshot = queue.snapshot()
        text = snapshot[0].text if snapshot else ""
        kind = snapshot[0].kind if snapshot else ""
        return len(snapshot), text, kind

    queued_count, first_text, first_kind = asyncio.run(run())
    assert queued_count == 1
    assert first_text == "second turn"
    # The first text was a plain prompt (no leading "/").
    assert first_kind == "prompt"


def test_streaming_session_running_input_buffer_clears_after_enter() -> None:
    """The running input buffer is wiped right after Enter pushes the line."""

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="…")
        # Yield to the event loop twice so the pipe input has time to
        # deliver the Enter keypress and the handler runs to completion.
        await asyncio.sleep(0.05)
        yield _agent_event("assistant_delta", text="…")
        await asyncio.sleep(0.05)
        yield _agent_event("completed", text="")

    async def run() -> str:
        queue = InputQueue()
        session = StreamingSession(queue=queue)
        with create_pipe_input() as pipe:
            pipe.send_text("queued prompt\r")
            await session.run(events(), input=pipe, output=DummyOutput())
        return session._running_buffer.text  # type: ignore[attr-defined]

    # After the session has exited, the buffer should be empty
    # because Enter cleared it before yielding control.
    assert asyncio.run(run()) == ""


def test_streaming_session_running_input_classifies_slash_command() -> None:
    """A running-turn ``/status`` is queued as ``kind == "slash"``."""

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="…")
        await asyncio.sleep(0.05)
        yield _agent_event("assistant_delta", text="…")
        await asyncio.sleep(0.05)
        yield _agent_event("completed", text="")

    async def run() -> str | None:
        queue = InputQueue()
        session = StreamingSession(queue=queue)
        with create_pipe_input() as pipe:
            pipe.send_text("/status\r")
            await session.run(events(), input=pipe, output=DummyOutput())
        snapshot = queue.snapshot()
        return snapshot[0].kind if snapshot else None

    assert asyncio.run(run()) == "slash"


def test_streaming_session_running_input_rejects_blank_submit() -> None:
    """Pressing Enter on an empty running buffer is a no-op."""

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="…")
        await asyncio.sleep(0.05)
        yield _agent_event("assistant_delta", text="…")
        await asyncio.sleep(0.05)
        yield _agent_event("completed", text="")

    async def run() -> int:
        queue = InputQueue()
        session = StreamingSession(queue=queue)
        with create_pipe_input() as pipe:
            # Just an Enter on an empty buffer.
            pipe.send_text("\r")
            await session.run(events(), input=pipe, output=DummyOutput())
        return len(queue)

    assert asyncio.run(run()) == 0


# --- queued preview rendering (execplan §M3) ------------------------------


def test_view_renders_queued_preview_with_limit() -> None:
    """``render_queued_inputs`` shows up to the limit and a summary line."""

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_view import (
        QUEUED_PREVIEW_LIMIT,
        render_queued_inputs,
    )

    queue = InputQueue()
    for index in range(QUEUED_PREVIEW_LIMIT + 2):
        queue.push(f"prompt {index}")
    lines = render_queued_inputs(queue.snapshot())
    # Header + one line per visible entry + overflow summary.
    assert lines[0] == "queued:"
    visible_rows = [
        line for line in lines[1:] if line.startswith("  - ")
    ]
    assert len(visible_rows) == QUEUED_PREVIEW_LIMIT
    summary_rows = [line for line in lines if "+" in line and "more queued" in line]
    assert summary_rows, "expected overflow summary line"
    assert f"+{len(queue) - QUEUED_PREVIEW_LIMIT} more queued" in summary_rows[0]


def test_view_renders_queued_preview_truncates_long_text() -> None:
    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_view import (
        QUEUED_PREVIEW_TEXT_LIMIT,
        render_queued_inputs,
    )

    long_text = "x" * (QUEUED_PREVIEW_TEXT_LIMIT * 3)
    queue = InputQueue()
    queue.push(long_text)
    lines = render_queued_inputs(queue.snapshot())
    # Find the bullet row.
    bullet = next(line for line in lines if line.startswith("  - "))
    # Visible text + gutter must be within budget. The truncation
    # marker is the trailing ellipsis character.
    rendered_text = bullet.removeprefix("  - ")
    assert rendered_text.endswith("…")
    assert len(rendered_text) <= QUEUED_PREVIEW_TEXT_LIMIT


def test_view_queued_preview_empty_when_no_inputs() -> None:
    from ui.cli.terminal.stream_view import render_queued_inputs

    # No inputs at all: view returns an empty list so callers can
    # safely ``extend`` the body without conditional branching.
    assert render_queued_inputs(None) == []
    assert render_queued_inputs(()) == []


def test_view_body_includes_queued_preview_when_passed() -> None:
    """``render_stream_body_ansi`` consumes the queue snapshot and
    inlines the queued preview between tools and errors.
    """

    from ui.cli.terminal.queue import InputQueue
    from ui.cli.terminal.stream_state import CliStreamUiState
    from ui.cli.terminal.stream_view import render_stream_body_ansi

    state = CliStreamUiState()
    queue = InputQueue()
    queue.push("second turn")
    queue.push("/status")
    ansi = render_stream_body_ansi(state, width=80, queued_inputs=queue.snapshot())
    rendered = ansi.value if hasattr(ansi, "value") else str(ansi)
    assert "queued:" in rendered
    assert "second turn" in rendered
    assert "/status" in rendered


# --- M4: queue drain (execplan §M4) ---------------------------------------


def test_repl_drains_queue_in_fifo_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """After a turn, ``InlineRepl`` drains queued inputs in FIFO order.
    Ordinary prompts go to ``_run_turn``; slash commands go to
    ``_handle_command``.
    """

    runtime = make_runtime(tmp_path)
    repl = InlineRepl(runtime)
    observed: list[tuple[str, str]] = []

    async def fake_run_turn(line: str) -> None:
        observed.append(("turn", line))

    async def fake_handle_command(line: str) -> None:
        observed.append(("command", line))

    monkeypatch.setattr(repl, "_run_turn", fake_run_turn)
    monkeypatch.setattr(repl, "_handle_command", fake_handle_command)

    repl._queue.push("first prompt")
    repl._queue.push("/status")
    repl._queue.push("second prompt")

    asyncio.run(repl._drain_queue())

    assert observed == [
        ("turn", "first prompt"),
        ("command", "/status"),
        ("turn", "second prompt"),
    ]
    # The queue is fully drained.
    assert len(repl._queue) == 0


def test_repl_drain_stops_on_runtime_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If a slash command nulls out the runtime (e.g. ``/exit``),
    the drain loop bails out instead of launching a new turn.
    """

    runtime = make_runtime(tmp_path)
    repl = InlineRepl(runtime)
    observed: list[str] = []

    async def fake_run_turn(line: str) -> None:
        observed.append(f"turn:{line}")

    async def fake_handle_command(line: str) -> None:
        observed.append(f"command:{line}")
        # Simulate ``/exit`` clearing the runtime.
        repl._runtime = None  # type: ignore[assignment]

    monkeypatch.setattr(repl, "_run_turn", fake_run_turn)
    monkeypatch.setattr(repl, "_handle_command", fake_handle_command)

    repl._queue.push("/exit")
    repl._queue.push("never runs")

    asyncio.run(repl._drain_queue())

    assert observed == ["command:/exit"]
    # The second queued item was never dispatched.
    assert len(repl._queue) == 1


def test_repl_run_turn_passes_queue_into_streaming_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``InlineRepl._run_turn`` constructs ``StreamingSession`` with
    the shared ``InputQueue`` and runtime so the running-turn input
    box can push to the same queue the REPL drains.
    """

    runtime = make_runtime(tmp_path)
    repl = InlineRepl(runtime)
    captured: dict[str, object] = {}

    class _StubSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, events):
            return None

    monkeypatch.setattr(repl_module, "StreamingSession", _StubSession)

    async def empty_events():
        if False:
            yield None

    async def invoke() -> None:
        # Patch the agent events iterator to a no-op so the test
        # doesn't require a real runtime loop.
        async def events():
            return
            yield  # pragma: no cover - makes this an async generator

        async def events_gen():  # type: ignore[no-redef]
            return
            yield  # pragma: no cover

        # Direct assign the agent events generator.
        repl._agent_events = lambda line: events_gen()  # type: ignore[assignment]
        await repl._run_turn("hi")

    asyncio.run(invoke())
    assert captured.get("queue") is repl._queue
    assert captured.get("runtime") is runtime
    # The agent_running flag flips back to False after the turn.
    assert repl._agent_running is False


# --- M5: transient selector + page ----------------------------------------


def test_selector_returns_choice_on_enter() -> None:
    from ui.cli.terminal.selector import SelectorItem, TransientSelector

    async def run():
        items = (
            SelectorItem(label="alpha", value=1),
            SelectorItem(label="beta", value=2),
            SelectorItem(label="gamma", value=3),
        )
        selector = TransientSelector("Pick", items)
        with create_pipe_input() as pipe:
            # Down once (to beta), then Enter.
            pipe.send_text("\x1b[B\r")
            return await selector.run(input=pipe, output=DummyOutput())

    chosen = asyncio.run(run())
    assert chosen is not None
    assert chosen.value == 2


def test_selector_cancels_on_escape() -> None:
    from ui.cli.terminal.selector import SelectorItem, TransientSelector

    async def run():
        items = (SelectorItem(label="alpha", value=1),)
        selector = TransientSelector("Pick", items)
        with create_pipe_input() as pipe:
            pipe.send_text("\x1b")  # Esc
            return await selector.run(input=pipe, output=DummyOutput())

    chosen = asyncio.run(run())
    assert chosen is None


def test_selector_empty_returns_none() -> None:
    from ui.cli.terminal.selector import TransientSelector

    async def run():
        selector = TransientSelector("Pick", ())
        with create_pipe_input() as pipe:
            return await selector.run(input=pipe, output=DummyOutput())

    assert asyncio.run(run()) is None


def test_connect_text_prompt_accepts_typed_input() -> None:
    from ui.cli.terminal.connect_flow import _prompt_text

    async def run() -> str | None:
        with create_pipe_input() as pipe:
            pipe.send_text("sk-secret\r")
            return await _prompt_text(
                "请输入 API Key",
                out=io.StringIO(),
                secret=True,
                input=pipe,
                output=DummyOutput(),
            )

    assert asyncio.run(run()) == "sk-secret"


def test_connect_text_prompt_cancels_on_escape() -> None:
    from ui.cli.terminal.connect_flow import _prompt_text

    async def run() -> str | None:
        with create_pipe_input() as pipe:
            pipe.send_text("\x1b")
            return await _prompt_text(
                "请输入 API Key",
                out=io.StringIO(),
                input=pipe,
                output=DummyOutput(),
            )

    assert asyncio.run(run()) is None


def test_connect_secret_prompt_does_not_mask_gutter() -> None:
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.processors import BeforeInput, PasswordProcessor

    from ui.cli.terminal.connect_flow import _build_text_prompt_application

    result: list[str | None] = [None]
    app = _build_text_prompt_application(
        "请输入 API Key",
        result,
        secret=True,
        input=None,
        output=DummyOutput(),
    )
    input_window = app.layout.container.children[1]
    control = input_window.content

    assert isinstance(control, BufferControl)
    assert isinstance(control.input_processors[0], PasswordProcessor)
    assert isinstance(control.input_processors[1], BeforeInput)


def test_page_closes_on_escape() -> None:
    from rich.text import Text

    from ui.cli.terminal.page import TransientPage

    async def run() -> None:
        page = TransientPage(Text("status content here"))
        with create_pipe_input() as pipe:
            pipe.send_text("\x1b")  # Esc closes the page
            await page.show(input=pipe, output=DummyOutput())

    # The page must return (not hang) once Esc is sent.
    asyncio.run(run())


def test_page_does_not_close_on_q_or_enter() -> None:
    from rich.text import Text

    from ui.cli.terminal.page import TransientPage

    async def run(keys: str) -> float:
        page = TransientPage(Text("status content here"))
        with create_pipe_input() as pipe:
            pipe.send_text(keys)

            async def close_later() -> None:
                await asyncio.sleep(0.05)
                pipe.send_text("\x1b")

            closer = asyncio.create_task(close_later())
            start = asyncio.get_running_loop().time()
            await page.show(input=pipe, output=DummyOutput())
            closer.cancel()
            return asyncio.get_running_loop().time() - start

    assert asyncio.run(run("q")) >= 0.04
    assert asyncio.run(run("\r")) >= 0.04


def test_page_renders_onecode_styles() -> None:
    from rich.table import Table

    from ui.cli.terminal.page import _render_to_ansi

    table = Table(header_style="onecode.subtle")
    table.add_column("field", style="onecode.subtle")
    table.add_row("value")
    rendered = _render_to_ansi(table, width=80)
    assert "field" in rendered
    assert "value" in rendered


# --- M5: permission modal choices -----------------------------------------


def test_permission_modal_choice_session() -> None:
    from services.permissions.types import PermissionOption
    from ui.cli.terminal.permission_modal import build_permission_choices

    class _Descriptor:
        name = "read_file"

    class _Request:
        descriptor = _Descriptor()
        options = (
            PermissionOption("allow_once", "allow once", "allow", "once"),
            PermissionOption(
                "allow_session_directory",
                "allow this directory for this session",
                "allow",
                "session",
            ),
            PermissionOption("deny", "deny", "deny", "once"),
        )

    response = build_permission_choices(_Request())[1].response
    assert response.action == "allow"
    assert response.scope == "session"


def test_permission_modal_choice_deny() -> None:
    from services.permissions.types import PermissionOption
    from ui.cli.terminal.permission_modal import build_permission_choices

    class _Descriptor:
        name = "bash"

    class _Request:
        descriptor = _Descriptor()
        options = (
            PermissionOption("allow_once", "allow once", "allow", "once"),
            PermissionOption(
                "allow_session_directory",
                "allow this directory for this session",
                "allow",
                "session",
            ),
            PermissionOption("deny", "deny", "deny", "once"),
        )

    response = build_permission_choices(_Request())[2].response
    assert response.action == "deny"
