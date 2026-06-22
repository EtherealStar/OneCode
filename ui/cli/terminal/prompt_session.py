"""The dynamic-region prompt input.

This module owns the bottom-of-screen prompt used in the inline REPL.
It is a non-full-screen :class:`prompt_toolkit.Application` that:

- has a top and bottom ``─`` border (Claude Code figure 1),
- shows the editable buffer behind a ``>`` gutter,
- floats a completion menu for ``/``-command and ``@``-file
  completion (:class:`ui.cli.terminal.completer.InlineCompleter`),
- treats **Enter** as "submit" and **Tab** as "fill but don't
  submit" when the completion menu is open.

The prompt session returns a structured :class:`PromptSubmission`
rather than a bare string so the REPL loop can distinguish
submit / cancel / exit without magic strings.

Note on running-turn input: queueing is **not** this module's
responsibility. The agent run-time path is implemented in
:class:`ui.cli.terminal.stream_session.StreamingSession` (see
``docs/exec-plans/active/cli-running-input-queue.md``), which
hosts its own prompt_toolkit input box at the bottom of the
dynamic region and pushes submissions onto the shared
:class:`InputQueue`. ``PromptSession`` only ever reads the
queue to construct the underlying :class:`InputQueue` reference
passed at construction; it never calls ``queue.push`` itself.

Enter/Tab semantics use prompt_toolkit's native
:attr:`Buffer.complete_state` as the single source of truth for "what
is highlighted", instead of mirroring an index ourselves. When the
menu is open with nothing explicitly selected we default to the first
completion, which matches the Claude Code figure-4 behaviour where
the top item is implicitly chosen.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.queue import InputQueue
from ui.cli.types import CliRuntime


class SubmissionKind(str, Enum):
    """How a prompt submission was triggered."""

    SUBMIT = "submit"
    CANCEL = "cancel"
    EXIT = "exit"


@dataclass(frozen=True)
class PromptSubmission:
    """Outcome of a single prompt session invocation."""

    kind: SubmissionKind
    text: str = ""


# prompt_toolkit style classes — all foreground-only so the terminal
# host's background wins. The borders use a dim grey to avoid clashing
# with light or dark profiles.
_PROMPT_STYLE = Style.from_dict(
    {
        "prompt-border": "#666666",
        "prompt-gutter": "ansicyan bold",
        "prompt-hint": "#666666",
        "suggestion": "#888888",
        "suggestion-current": "ansicyan bold",
        "suggestion-meta": "#777777",
        "suggestion-meta-current": "ansiwhite",
    }
)

_EXIT_CONFIRM_HINT = "Press Ctrl-C again to exit"
_OSC11_REPLY_FRAGMENT = re.compile(
    r"(?:\x1b)?\]11;rgb:[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}(?:\x07|\x1b\\|\\)?"
)


def strip_osc11_reply_fragments(text: str) -> str:
    """Remove the narrow OSC 11 reply fragment known to leak into input."""

    return _OSC11_REPLY_FRAGMENT.sub("", text)


def _highlighted_completion(buffer: Buffer) -> Completion | None:
    """Return the completion that Enter/Tab should act on.

    Resolution order:

    1. The completion the user explicitly navigated to
       (``complete_state.current_completion``).
    2. The first completion in an already-open menu.
    3. A synchronously-computed first completion. ``complete_while_typing``
       populates ``complete_state`` from a background task, so when the
       prompt is driven quickly (or headlessly in tests) the menu may
       not have opened yet by the time Enter fires. Computing the
       completer directly closes that race without auto-accepting in a
       non-completion context — the completer returns nothing for plain
       text, so this stays ``None`` and the literal line is submitted.
    """

    state = buffer.complete_state
    if state is not None:
        if state.current_completion is not None:
            return state.current_completion
        if state.completions:
            return state.completions[0]
    completer = buffer.completer
    if completer is None:
        return None
    completions = list(
        completer.get_completions(buffer.document, CompleteEvent())
    )
    if completions:
        return completions[0]
    return None


def _completion_kind(completion: Completion) -> str | None:
    item = getattr(completion, "_suggestion_item", None)
    kind = getattr(item, "kind", None)
    return kind if isinstance(kind, str) else None


def _directory_mention_token_end(buffer: Buffer) -> int | None:
    text = buffer.text
    cursor = buffer.cursor_position
    at_index = text.rfind("@", 0, min(cursor + 1, len(text)))
    if at_index < 0:
        return None
    if at_index > 0 and not text[at_index - 1].isspace():
        return None
    end = cursor
    while end < len(text) and not text[end].isspace():
        end += 1
    token = text[at_index + 1 : end]
    if not token or not token.endswith("/"):
        return None
    return end


def _apply_completion_for_edit(buffer: Buffer, completion: Completion) -> None:
    buffer.apply_completion(completion)
    kind = _completion_kind(completion)
    if kind == "file" and not buffer.text.endswith(" "):
        buffer.insert_text(" ")


class PromptSession:
    """A reusable wrapper around a prompt_toolkit Application."""

    def __init__(
        self,
        runtime: CliRuntime | None,
        queue: InputQueue,
        *,
        bottom_hint: str = "",
        exit_confirm_window_seconds: float = 1.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runtime = runtime
        self._queue = queue
        self._bottom_hint = bottom_hint
        self._exit_confirm_window_seconds = exit_confirm_window_seconds
        self._clock = clock
        self._completer = InlineCompleter(runtime)
        self._pending_exit_at: float | None = None
        self._suppress_next_text_reset = False
        self._active_hint: _PromptHint | None = None

    async def read(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> PromptSubmission:
        """Block until the user submits, cancels, or exits.

        ``input``/``output`` are injection points for tests, which
        pass a :func:`prompt_toolkit.input.create_pipe_input` pipe and
        a :class:`prompt_toolkit.output.DummyOutput`. In production both
        are ``None`` and prompt_toolkit binds to the real terminal.
        """

        result: list[PromptSubmission | None] = [None]
        app = self._build_application(result, input=input, output=output)
        await app.run_async()
        # Ctrl-C/Ctrl-D handlers always set a result; a clean exit
        # without a handler firing is treated as a cancel.
        return result[0] or PromptSubmission(kind=SubmissionKind.CANCEL)

    # --- internal ----------------------------------------------------------

    def _build_application(
        self,
        result: list[PromptSubmission | None],
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        buffer = Buffer(
            completer=self._completer,
            complete_while_typing=True,
            multiline=False,
            on_text_changed=self._on_buffer_text_changed,
        )
        hint = _PromptHint(self._current_bottom_hint())
        self._active_hint = hint
        buffer_control = BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput("> ", style="class:prompt-gutter")],
            include_default_input_processors=True,
        )
        bindings = self._build_key_bindings(buffer, result, hint)

        prompt_window = Window(
            content=buffer_control,
            height=Dimension(min=1, max=1),
            wrap_lines=False,
        )
        suggestion_panel = _suggestion_panel(buffer)
        body = HSplit(
            [
                _spacer_window(),
                _border_window(),
                prompt_window,
                suggestion_panel,
                _hint_window(buffer, hint.text),
                _border_window(),
            ]
        )
        return Application(
            layout=Layout(body, focused_element=prompt_window),
            style=_PROMPT_STYLE,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )

    def _build_key_bindings(
        self,
        buffer: Buffer,
        result: list[PromptSubmission | None],
        hint: "_PromptHint",
    ) -> KeyBindings:
        bindings = KeyBindings()

        def finish(submission: PromptSubmission, event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            result[0] = submission
            event.app.exit()

        @bindings.add(Keys.Enter, eager=True)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            directory_mention_end = _directory_mention_token_end(buffer)
            if directory_mention_end is not None:
                if buffer.complete_state is not None:
                    buffer.cancel_completion()
                buffer.cursor_position = directory_mention_end
                if (
                    directory_mention_end >= len(buffer.text)
                    or buffer.text[directory_mention_end] != " "
                ):
                    buffer.insert_text(" ")
                hint.reset()
                event.app.invalidate()
                return
            completion = _highlighted_completion(buffer)
            if completion is not None:
                kind = _completion_kind(completion)
                if kind in {"file", "directory"}:
                    # File mentions are usually followed by a natural-language
                    # request, so accepting them keeps the prompt open.
                    _apply_completion_for_edit(buffer, completion)
                    hint.reset()
                    event.app.invalidate()
                    return
                # Command/session completion is a complete input target:
                # accept the highlighted item and submit it immediately.
                buffer.apply_completion(completion)
                text = buffer.text.strip()
                if text:
                    finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)
                return
            text = buffer.text.strip()
            if not text:
                return
            finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)

        @bindings.add(Keys.Tab, eager=True)
        def _on_tab(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            completion = _highlighted_completion(buffer)
            if completion is not None:
                # Menu open + Tab → fill the input with the item but do
                # NOT submit. The next Enter submits it.
                _apply_completion_for_edit(buffer, completion)
                return
            # No menu: trigger completion so the user sees suggestions.
            buffer.start_completion(select_first=False)

        @bindings.add(Keys.Down, eager=True)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            if buffer.complete_state is not None:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=True)

        @bindings.add(Keys.Up, eager=True)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            self._reset_pending_exit()
            hint.reset()
            if buffer.complete_state is not None:
                buffer.complete_previous()

        @bindings.add(Keys.ControlC, eager=True)
        def _on_ctrl_c(event) -> None:  # type: ignore[no-untyped-def]
            now = self._clock()
            if self._pending_exit_at is not None:
                elapsed = now - self._pending_exit_at
                if elapsed <= self._exit_confirm_window_seconds:
                    finish(PromptSubmission(SubmissionKind.EXIT), event)
                    return
            self._pending_exit_at = now
            hint.set(_EXIT_CONFIRM_HINT)
            asyncio.create_task(self._expire_exit_hint_after(now, hint, event.app))
            if buffer.complete_state is not None:
                buffer.cancel_completion()
            if buffer.text:
                self._suppress_next_text_reset = True
                buffer.text = ""
            event.app.invalidate()

        @bindings.add(Keys.ControlD, eager=True)
        def _on_ctrl_d(event) -> None:  # type: ignore[no-untyped-def]
            # Shell-style EOF: empty buffer + Ctrl-D exits the REPL.
            if not buffer.text:
                finish(PromptSubmission(SubmissionKind.EXIT), event)

        @bindings.add(Keys.Escape, eager=True)
        def _on_escape(event) -> None:  # type: ignore[no-untyped-def]
            # Esc closes the completion menu if open; otherwise no-op.
            if buffer.complete_state is not None:
                buffer.cancel_completion()

        return bindings

    def _on_buffer_text_changed(self, buffer: Buffer) -> None:
        cleaned = strip_osc11_reply_fragments(buffer.text)
        if cleaned != buffer.text:
            cursor = min(buffer.cursor_position, len(cleaned))
            self._suppress_next_text_reset = True
            buffer.text = cleaned
            buffer.cursor_position = cursor
            return
        if self._suppress_next_text_reset:
            self._suppress_next_text_reset = False
            return
        if buffer.text:
            self._reset_pending_exit()
            if self._active_hint is not None:
                self._active_hint.reset()

    def _reset_pending_exit(self) -> None:
        self._pending_exit_at = None

    def _current_bottom_hint(self) -> str:
        if self._bottom_hint:
            return self._bottom_hint
        state = getattr(self._runtime, "state", None)
        if state is not None and state.is_plan_mode():
            return "plan mode on"
        return ""

    async def _expire_exit_hint_after(
        self,
        timestamp: float,
        hint: "_PromptHint",
        app: Application[None],
    ) -> None:
        await asyncio.sleep(self._exit_confirm_window_seconds)
        if self._pending_exit_at != timestamp:
            return
        self._reset_pending_exit()
        hint.reset()
        app.invalidate()


# --- layout helpers --------------------------------------------------------


def _border_window() -> Window:
    return Window(
        height=Dimension(min=1, max=1),
        char="─",
        style="class:prompt-border",
    )


def _spacer_window() -> Window:
    return Window(height=Dimension(min=1, max=1), char="")


def _hint_window(buffer: Buffer, hint: Callable[[], str]) -> ConditionalContainer:
    window = Window(
        height=Dimension(min=1, max=1),
        content=FormattedTextControl(lambda: [("class:prompt-hint", _hint_text(buffer, hint))]),
        style="class:prompt-hint",
    )
    return ConditionalContainer(
        window,
        filter=Condition(lambda: bool(_hint_text(buffer, hint))),
    )


def _hint_text(buffer: Buffer, hint: Callable[[], str]) -> str:
    text = hint()
    if text:
        return text
    if _suggestion_rows(buffer):
        return "Enter to accept · Tab to fill · ↑↓ to choose"
    return ""


class _PromptHint:
    def __init__(self, default: str) -> None:
        self._default = default
        self._text = default

    def text(self) -> str:
        return self._text

    def set(self, text: str) -> None:
        self._text = text

    def reset(self) -> None:
        self._text = self._default


def _suggestion_panel(buffer: Buffer) -> ConditionalContainer:
    window = Window(
        height=Dimension(min=1, max=8),
        content=FormattedTextControl(lambda: _suggestion_fragments(buffer)),
        dont_extend_height=True,
        style="class:suggestion",
    )
    return ConditionalContainer(
        window,
        filter=Condition(lambda: bool(_suggestion_rows(buffer))),
    )


def _suggestion_rows(buffer: Buffer) -> tuple[tuple[Completion, bool], ...]:
    state = buffer.complete_state
    if state is None or not state.completions:
        if buffer.completer is None:
            return ()
        completions = list(
            buffer.completer.get_completions(buffer.document, CompleteEvent())
        )
        return tuple(
            (completion, index == 0)
            for index, completion in enumerate(completions[:8])
        )
    completions = tuple(state.completions[:8])
    if not completions:
        return ()
    current = state.current_completion
    if current is None:
        current_index = 0
    else:
        try:
            current_index = completions.index(current)
        except ValueError:
            current_index = 0
    return tuple(
        (completion, index == current_index)
        for index, completion in enumerate(completions)
    )


def _suggestion_fragments(buffer: Buffer) -> FormattedText:
    rows = _suggestion_rows(buffer)
    if not rows:
        return FormattedText([])
    command_width = min(
        max(len(completion.display_text) for completion, _ in rows),
        32,
    )
    fragments: list[tuple[str, str]] = []
    for index, (completion, selected) in enumerate(rows):
        display = completion.display_text
        meta = completion.display_meta_text
        pointer = "> " if selected else "  "
        display_style = "class:suggestion-current" if selected else "class:suggestion"
        meta_style = (
            "class:suggestion-meta-current" if selected else "class:suggestion-meta"
        )
        fragments.append((display_style, pointer))
        fragments.append((display_style, display.ljust(command_width)))
        if meta:
            fragments.append((meta_style, f"  {meta}"))
        if index < len(rows) - 1:
            fragments.append(("", "\n"))
    return FormattedText(fragments)


__all__ = [
    "PromptSession",
    "PromptSubmission",
    "SubmissionKind",
    "strip_osc11_reply_fragments",
]
