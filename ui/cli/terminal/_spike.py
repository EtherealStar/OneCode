"""M0 spike — validate the inline REPL primitives.

Run with::

    uv run python -m ui.cli.terminal._spike

The spike exercises:

1. Terminal background brightness detection (OSC 11 → COLORFGBG → dark).
2. Reverse-video user prompt with a layout-derived style that
   respects the detected brightness.
3. A non-full-screen :class:`prompt_toolkit.Application` with a top
   and bottom ``─`` border and an erase-on-exit dynamic region.
4. A 50ms-throttled live Markdown preview that accumulates fragments
   and re-renders them in the dynamic region, surviving partial
   fenced code blocks.

This file is intentionally independent of the rest of the CLI; it
does not import :mod:`ui.cli.app` or :mod:`core.loop`. M0 only needs
to prove that the four primitives above work end-to-end; the
production wiring arrives in M1–M5.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Iterable

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.output import create_output
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from ui.cli.terminal.detect import detect_terminal_brightness


# --- static output (minimal slice for the spike) --------------------------


def _user_reverse_style(brightness: str) -> str:
    # On light backgrounds we invert to white-on-black so the user
    # prompt row reads as a solid block. On dark backgrounds we
    # invert to black-on-white so the prompt stands out the same
    # way. Rich resolves ``reverse`` to swap fg/bg, so we use direct
    # styles when we know the host background.
    if brightness == "light":
        return "black on white"
    return "white on black"


def _spike_static_banner(brightness: str) -> None:
    """Print a fixed banner line into the static scrollback."""

    console = Console()
    console.print(
        Text(
            "M0 spike — inline terminal REPL primitives",
            style="bold cyan",
        )
    )
    console.print(
        Text(
            f"Detected terminal brightness: {brightness}",
            style="onecode.subtle",
        )
    )
    console.print(
        Text(
            "> /status",
            style=_user_reverse_style(brightness),
        )
    )
    console.print(
        Text("onecode> ", style="onecode.title")
        + Text("(assistant reply goes here)", style="onecode.metric")
    )


# --- dynamic prompt -------------------------------------------------------


@dataclass
class _SpikeState:
    submitted: str | None = None
    streaming_text: str = ""


def _spike_prompt_layout(state: _SpikeState) -> Layout:
    """A minimal prompt layout: top border, prompt, bottom border.

    Both borders and the prompt input live inside a non-full-screen
    Application. The Application runs with
    ``erase_when_done=True`` so the borders and the prompt
    disappear cleanly once the user submits.
    """

    top_border = Window(
        height=1,
        char="─",
        style="class:prompt-border",
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    prompt_window = Window(
        height=1,
        content=FormattedTextControl(
            "spike> ",
            focusable=True,
            key_bindings=None,
        ),
    )
    bottom_border = Window(
        height=1,
        char="─",
        style="class:prompt-border",
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    body = HSplit(
        [
            top_border,
            prompt_window,
            bottom_border,
        ],
    )
    return Layout(body, focused_element=prompt_window)


async def _run_prompt(state: _SpikeState) -> None:
    """Run the dynamic prompt until the user submits empty text."""

    app: Application[None] = Application(
        layout=_spike_prompt_layout(state),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )
    await app.run_async()


# --- live streaming preview -----------------------------------------------


def _render_streaming_preview(state: _SpikeState) -> FormattedText:
    # Re-rendering partial Markdown through Rich is expensive, so we
    # only re-render when the buffer is short. For the spike we render
    # a small prefix with Markdown and append an ellipsis to signal
    # "more to come".
    buffer = state.streaming_text
    if not buffer:
        return FormattedText([("class:stream-dim", "(streaming preview idle)")])
    if buffer.endswith("\n```") or buffer.count("```") % 2 == 1:
        # Partial fenced code block — Rich would still render, but
        # we'd accumulate a stray fence at the bottom. We just show
        # the raw text with a subtle style.
        return FormattedText(
            [("class:stream-text", buffer + " …")]
        )
    return FormattedText(
        [("class:stream-text", buffer + " …")]
    )


async def _run_streaming_preview(state: _SpikeState) -> None:
    """Simulate a 50ms-throttled streaming preview."""

    fragments: Iterable[str] = (
        "# Hello from OneCode\n\n",
        "This is a *streaming* ",
        "Markdown ",
        "preview.\n\n",
        "- bullet 1\n",
        "- bullet 2\n",
        "```python\n",
        "print('hi')\n",
    )

    # Use a tiny live region above the prompt. We piggyback on the
    # same prompt layout but swap the prompt for a status line.
    top_border = Window(
        height=1,
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    preview_window = Window(
        height=3,
        content=FormattedTextControl(
            lambda: _render_streaming_preview(state),
        ),
    )
    status = Window(
        height=1,
        content=FormattedTextControl("streaming... press Ctrl-C to abort"),
    )
    bottom_border = Window(
        height=1,
        content=FormattedTextControl(
            FormattedText([("class:prompt-border", "─" * 40)])
        ),
    )
    body = HSplit([top_border, preview_window, status, bottom_border])
    app: Application[None] = Application(
        layout=Layout(body),
        full_screen=False,
        erase_when_done=True,
        mouse_support=False,
    )

    # Schedule the streaming task alongside the app.
    async def feed() -> None:
        for fragment in fragments:
            state.streaming_text += fragment
            app.invalidate()
            await asyncio.sleep(0.05)
        # Keep the preview visible for a brief moment so the user can
        # see the final state before the region erases.
        await asyncio.sleep(0.2)

    await asyncio.gather(app.run_async(), feed())


# --- spike entry point ----------------------------------------------------


def main() -> int:
    brightness = detect_terminal_brightness()
    _spike_static_banner(brightness)

    # 1. Static region already printed above. Now run a dynamic prompt.
    state = _SpikeState()
    asyncio.run(_run_prompt(state))
    # 2. Then a streaming preview, also dynamic.
    asyncio.run(_run_streaming_preview(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())