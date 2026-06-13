"""The dynamic-region prompt input.

This module owns the bottom-of-screen prompt used in the inline REPL.
It is a non-full-screen :class:`prompt_toolkit.Application` that:

- has a top and bottom ``─`` border (Claude Code figure 1),
- shows the editable buffer behind a ``>`` gutter,
- floats a completion menu for ``/``-command and ``@``-file
  completion (:class:`ui.cli.terminal.completer.InlineCompleter`),
- treats **Enter** as "submit" and **Tab** as "fill but don't
  submit" when the completion menu is open,
- supports queueing: while ``queue_mode`` is set, ``Enter`` during a
  running agent turn pushes the line onto the queue instead of
  starting a new turn.

The prompt session returns a structured :class:`PromptSubmission`
rather than a bare string so the REPL loop can distinguish
submit / queue / cancel / exit without magic strings.

Enter/Tab semantics use prompt_toolkit's native
:attr:`Buffer.complete_state` as the single source of truth for "what
is highlighted", instead of mirroring an index ourselves. When the
menu is open with nothing explicitly selected we default to the first
completion, which matches the Claude Code figure-4 behaviour where
the top item is implicitly chosen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import (
    ConditionalContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style

from ui.cli.terminal.completer import InlineCompleter
from ui.cli.terminal.queue import InputQueue
from ui.cli.types import CliRuntime


class SubmissionKind(str, Enum):
    """How a prompt submission was triggered."""

    SUBMIT = "submit"
    QUEUE = "queue"
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
        "completion-menu.completion": "bg:default",
        "completion-menu.completion.current": "bg:ansicyan fg:ansiblack",
        "completion-menu.meta.completion": "#888888",
        "completion-menu.meta.completion.current": "bg:ansibrightblack fg:ansiwhite",
    }
)


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


class PromptSession:
    """A reusable wrapper around a prompt_toolkit Application."""

    def __init__(
        self,
        runtime: CliRuntime | None,
        queue: InputQueue,
        *,
        bottom_hint: str = "Enter to send · Tab to fill · ↑↓ to choose · Ctrl-C to cancel",
    ) -> None:
        self._runtime = runtime
        self._queue = queue
        self._bottom_hint = bottom_hint
        self._completer = InlineCompleter(runtime)

    async def read(
        self,
        *,
        queue_mode: bool,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> PromptSubmission:
        """Block until the user submits, queues, cancels, or exits.

        ``input``/``output`` are injection points for tests, which
        pass a :func:`prompt_toolkit.input.create_pipe_input` pipe and
        a :class:`prompt_toolkit.output.DummyOutput`. In production both
        are ``None`` and prompt_toolkit binds to the real terminal.
        """

        result: list[PromptSubmission | None] = [None]
        app = self._build_application(queue_mode, result, input=input, output=output)
        await app.run_async()
        # Ctrl-C/Ctrl-D handlers always set a result; a clean exit
        # without a handler firing is treated as a cancel.
        return result[0] or PromptSubmission(kind=SubmissionKind.CANCEL)

    # --- internal ----------------------------------------------------------

    def _build_application(
        self,
        queue_mode: bool,
        result: list[PromptSubmission | None],
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        buffer = Buffer(
            completer=self._completer,
            complete_while_typing=True,
            multiline=False,
        )
        gutter = "▌ " if queue_mode else "> "
        buffer_control = BufferControl(
            buffer=buffer,
            input_processors=[BeforeInput(gutter, style="class:prompt-gutter")],
            include_default_input_processors=True,
        )
        bindings = self._build_key_bindings(buffer, queue_mode, result)

        prompt_window = Window(
            content=buffer_control,
            height=Dimension(min=1, max=1),
            wrap_lines=False,
        )
        body = HSplit(
            [
                _border_window(),
                FloatContainer(
                    content=prompt_window,
                    floats=[
                        Float(
                            xcursor=True,
                            ycursor=True,
                            content=CompletionsMenu(max_height=8, scroll_offset=1),
                        ),
                    ],
                ),
                _hint_window(self._bottom_hint),
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
        queue_mode: bool,
        result: list[PromptSubmission | None],
    ) -> KeyBindings:
        bindings = KeyBindings()

        def finish(submission: PromptSubmission, event) -> None:  # type: ignore[no-untyped-def]
            result[0] = submission
            event.app.exit()

        @bindings.add(Keys.Enter, eager=True)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            completion = _highlighted_completion(buffer)
            if completion is not None:
                # Menu open + Enter → accept the highlighted item and
                # submit it immediately.
                buffer.apply_completion(completion)
                text = buffer.text.strip()
                if text:
                    finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)
                return
            text = buffer.text.strip()
            if not text:
                return
            if queue_mode:
                # Agent is running: queue the line and report it back.
                self._queue.push(text)
                finish(PromptSubmission(SubmissionKind.QUEUE, text), event)
                return
            finish(PromptSubmission(SubmissionKind.SUBMIT, text), event)

        @bindings.add(Keys.Tab, eager=True)
        def _on_tab(event) -> None:  # type: ignore[no-untyped-def]
            completion = _highlighted_completion(buffer)
            if completion is not None:
                # Menu open + Tab → fill the input with the item but do
                # NOT submit. The next Enter submits it.
                buffer.apply_completion(completion)
                return
            # No menu: trigger completion so the user sees suggestions.
            buffer.start_completion(select_first=False)

        @bindings.add(Keys.Down, eager=True)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            if buffer.complete_state is not None:
                buffer.complete_next()
            else:
                buffer.start_completion(select_first=True)

        @bindings.add(Keys.Up, eager=True)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            if buffer.complete_state is not None:
                buffer.complete_previous()

        @bindings.add(Keys.ControlC, eager=True)
        def _on_ctrl_c(event) -> None:  # type: ignore[no-untyped-def]
            finish(PromptSubmission(SubmissionKind.CANCEL), event)

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


# --- layout helpers --------------------------------------------------------


def _border_window() -> Window:
    return Window(
        height=Dimension(min=1, max=1),
        char="─",
        style="class:prompt-border",
    )


def _hint_window(hint: str) -> ConditionalContainer:
    from prompt_toolkit.filters import Condition

    window = Window(
        height=Dimension(min=1, max=1),
        content=FormattedTextControl([("class:prompt-hint", hint)]),
        style="class:prompt-hint",
    )
    # Always shown; ConditionalContainer keeps the door open for
    # hiding the hint on very short terminals later.
    return ConditionalContainer(window, filter=Condition(lambda: True))


__all__ = [
    "PromptSession",
    "PromptSubmission",
    "SubmissionKind",
]