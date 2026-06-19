"""Full-screen ``/status``-style pages shown on the alternate screen.

A *page* is a Rich renderable that should occupy the whole terminal
window while the user reads it, with ``Esc`` returning to the inline REPL.
Pages are rendered inside a
``full_screen`` :class:`prompt_toolkit.Application`, which manages the
alternate screen (DEC 1049) itself — so the user's static scrollback
is preserved unchanged and the page content never leaks into it.

We render the Rich renderable to ANSI text once and display it in a
scrollable window. ``↑``/``↓``/``PageUp``/``PageDown`` scroll; ``Esc``
closes. This is the "simple" page from execplan §M5 — no tabs, no live
refresh.
"""

from __future__ import annotations

import io
import sys
from typing import Any, TextIO

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from rich.console import Console

from ui.cli.terminal.transient import can_enter_alternate_screen
from ui.cli.theme import RICH_THEME


def _render_to_ansi(renderable: Any, *, width: int) -> str:
    """Render a Rich renderable to an ANSI string for prompt_toolkit."""

    out = io.StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=RICH_THEME,
    )
    console.print(renderable)
    return out.getvalue()


class TransientPage:
    """Render a single Rich renderable full-screen until the user exits."""

    def __init__(
        self,
        renderable: Any,
        *,
        stdout: TextIO | None = None,
    ) -> None:
        self._renderable = renderable
        self._stdout = stdout if stdout is not None else sys.stdout
        self._scroll = 0

    async def show(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> None:
        """Display the page; block until the user closes it.

        On non-TTY hosts (where the alternate screen is unavailable)
        the page is a no-op — callers should detect this and fall back
        to inline printing instead.
        """

        if input is None and output is None and not can_enter_alternate_screen(
            self._stdout
        ):
            return

        lines = _PageLines(self._renderable)
        bindings = KeyBindings()

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _close(event) -> None:  # type: ignore[no-untyped-def]
            event.app.exit()

        @bindings.add(Keys.Down, eager=True)
        def _down(event) -> None:  # type: ignore[no-untyped-def]
            lines.scroll(1)
            event.app.invalidate()

        @bindings.add(Keys.Up, eager=True)
        def _up(event) -> None:  # type: ignore[no-untyped-def]
            lines.scroll(-1)
            event.app.invalidate()

        @bindings.add(Keys.PageDown, eager=True)
        def _page_down(event) -> None:  # type: ignore[no-untyped-def]
            lines.scroll(10)
            event.app.invalidate()

        @bindings.add(Keys.PageUp, eager=True)
        def _page_up(event) -> None:  # type: ignore[no-untyped-def]
            lines.scroll(-10)
            event.app.invalidate()

        def body_text():  # type: ignore[no-untyped-def]
            try:
                size = app.output.get_size()
                width, height = size.columns, size.rows
            except Exception:
                width, height = 80, 24
            return ANSI(lines.window(width=width, height=height - 1))

        def footer_text():  # type: ignore[no-untyped-def]
            from prompt_toolkit.formatted_text import FormattedText

            return FormattedText(
                [("class:page-footer", " Esc to return · ↑↓ to scroll ")]
            )

        body = Window(content=FormattedTextControl(body_text))
        footer = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(footer_text),
        )
        app: Application[None] = Application(
            layout=Layout(HSplit([body, footer])),
            full_screen=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )
        await app.run_async()


class _PageLines:
    """Lazily render + cache the page's ANSI lines and track scroll."""

    def __init__(self, renderable: Any) -> None:
        self._renderable = renderable
        self._cache_width: int | None = None
        self._lines: list[str] = []
        self._offset = 0

    def _ensure(self, width: int) -> None:
        if self._cache_width == width:
            return
        rendered = _render_to_ansi(self._renderable, width=width)
        self._lines = rendered.splitlines()
        self._cache_width = width

    def scroll(self, delta: int) -> None:
        self._offset = max(0, self._offset + delta)

    def window(self, *, width: int, height: int) -> str:
        self._ensure(width)
        max_offset = max(0, len(self._lines) - height)
        self._offset = min(self._offset, max_offset)
        visible = self._lines[self._offset : self._offset + height]
        return "\n".join(visible)
