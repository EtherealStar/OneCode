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
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from ui.cli.terminal import static_output as so
from ui.cli.terminal import transient
from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.prompt_session import (
    PromptSession,
    PromptSubmission,
    SubmissionKind,
    strip_osc11_reply_fragments,
)
from ui.cli.terminal.queue import InputQueue
from ui.cli.theme import RICH_THEME


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


def test_assistant_prefix_is_printed(captured_console: io.StringIO) -> None:
    so.print_assistant_start()
    output = captured_console.getvalue()
    assert "onecode>" in output


def test_assistant_markdown_renders_body(captured_console: io.StringIO) -> None:
    so.print_assistant_markdown("plain reply")
    output = captured_console.getvalue()
    assert "plain reply" in output


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
    assert queue.pop() == "first"
    assert queue.pop() == "second"
    assert queue.pop() is None


def test_queue_skips_blank_lines() -> None:
    queue = InputQueue()
    queue.push("   ")
    queue.push("")
    assert len(queue) == 0


def test_queue_snapshot_is_readonly_copy() -> None:
    queue = InputQueue()
    queue.push("a")
    snapshot = queue.snapshot()
    queue.push("b")
    # snapshot was taken before "b" was pushed.
    assert snapshot == ("a",)


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
    *,
    queue_mode: bool = False,
) -> PromptSubmission:
    """Feed ``keys`` into a PromptSession via a pipe input."""

    async def run() -> PromptSubmission:
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            return await session.read(
                queue_mode=queue_mode,
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


def test_enter_plain_text_submits_literally(tmp_path: Path) -> None:
    session = PromptSession(_FakeRuntime(tmp_path), InputQueue())
    submission = _drive_prompt(session, "hello world\r")
    assert submission.kind is SubmissionKind.SUBMIT
    assert submission.text == "hello world"


def test_enter_in_queue_mode_pushes_to_queue(tmp_path: Path) -> None:
    queue = InputQueue()
    session = PromptSession(_FakeRuntime(tmp_path), queue)
    submission = _drive_prompt(session, "second turn\r", queue_mode=True)
    assert submission.kind is SubmissionKind.QUEUE
    assert submission.text == "second turn"
    assert queue.pop() == "second turn"


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


# --- M4: streaming session ------------------------------------------------


def _agent_event(event_type: str, **kwargs):
    from core.stream_events import AgentEvent

    return AgentEvent(type=event_type, **kwargs)


def test_consume_event_accumulates_deltas() -> None:
    from ui.cli.terminal.stream_session import StreamBuffer, consume_event

    buffer = StreamBuffer()
    consume_event(buffer, _agent_event("assistant_delta", text="Hello "))
    consume_event(buffer, _agent_event("assistant_delta", text="world"))
    assert buffer.text == "Hello world"


def test_consume_event_completed_fallback_text() -> None:
    from ui.cli.terminal.stream_session import StreamBuffer, consume_event

    buffer = StreamBuffer()
    # No deltas, only a completed event carrying the full text.
    consume_event(buffer, _agent_event("completed", text="final answer"))
    assert buffer.text == "final answer"


def test_consume_event_tool_banner_to_static(captured_console: io.StringIO) -> None:
    from ui.cli.terminal.stream_session import StreamBuffer, consume_event

    class _Call:
        id = "call_9"
        name = "grep"
        input = {"pattern": "TODO"}

    buffer = StreamBuffer()
    consume_event(
        buffer,
        _agent_event("tool_call_ready", metadata={"tool_call": _Call()}),
    )
    output = captured_console.getvalue()
    assert "grep" in output
    assert "call_9" in output
    assert buffer.tool_banner_count == 1


def test_render_preview_handles_partial_code_fence() -> None:
    from ui.cli.terminal.stream_session import StreamBuffer, render_preview_ansi

    buffer = StreamBuffer(text="```python\nprint('hi')\n")  # unbalanced fence
    ansi = render_preview_ansi(buffer, width=60)
    # Must not raise and must include the code text without a synthetic
    # closing fence that the Markdown renderer would add.
    rendered = ansi.value if hasattr(ansi, "value") else str(ansi)
    assert "print" in rendered


def test_render_preview_bounded_height() -> None:
    from ui.cli.terminal.stream_session import (
        StreamBuffer,
        _PREVIEW_MAX_LINES,
        render_preview_ansi,
    )

    many_lines = "\n\n".join(f"line {i}" for i in range(100))
    buffer = StreamBuffer(text=many_lines)
    ansi = render_preview_ansi(buffer, width=60)
    rendered = ansi.value if hasattr(ansi, "value") else str(ansi)
    # Bounded to the preview window plus the truncation marker.
    assert rendered.count("\n") <= _PREVIEW_MAX_LINES + 1


def test_streaming_session_drains_and_commits(captured_console: io.StringIO) -> None:
    from ui.cli.terminal.stream_session import StreamingSession

    async def events():
        yield _agent_event("assistant_delta", text="part one ")
        yield _agent_event("assistant_delta", text="part two")
        yield _agent_event("completed", text="")

    async def run() -> None:
        session = StreamingSession()
        with create_pipe_input() as pipe:
            buffer = await session.run(
                events(),
                input=pipe,
                output=DummyOutput(),
            )
        assert buffer.text == "part one part two"
        assert not session.cancelled

    asyncio.run(run())
    # The final text is committed to the static region as Markdown.
    assert "part one part two" in captured_console.getvalue()


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


def test_page_renders_onecode_styles() -> None:
    from rich.table import Table

    from ui.cli.terminal.page import _render_to_ansi

    table = Table(header_style="onecode.subtle")
    table.add_column("field", style="onecode.subtle")
    table.add_row("value")
    rendered = _render_to_ansi(table, width=80)
    assert "field" in rendered
    assert "value" in rendered


# --- M5: permission response mapping --------------------------------------


def test_permission_response_for_choice_session() -> None:
    from ui.cli.terminal.permission_prompt import _response_for_choice

    class _Descriptor:
        name = "read_file"

    class _Request:
        descriptor = _Descriptor()

    response = _response_for_choice("s", _Request())
    assert response.action == "allow"
    assert response.scope == "session"


def test_permission_response_for_choice_deny() -> None:
    from ui.cli.terminal.permission_prompt import _response_for_choice

    class _Descriptor:
        name = "bash"

    class _Request:
        descriptor = _Descriptor()

    response = _response_for_choice("n", _Request())
    assert response.action == "deny"
