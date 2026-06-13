"""List selector for ``/resume`` (and similar) on the alternate screen.

The selector renders one row per item and lets the user pick one.
``Esc`` cancels and returns ``None``; arrow keys move the highlight;
``Enter`` selects. The selector is a ``full_screen``
:class:`prompt_toolkit.Application`, which manages the alternate
screen (DEC 1049) itself so the selection UI never leaks into the
static scrollback.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Generic, Sequence, TextIO, TypeVar

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from ui.cli.terminal.transient import can_enter_alternate_screen


T = TypeVar("T")


@dataclass(frozen=True)
class SelectorItem(Generic[T]):
    label: str
    value: T
    detail: str = ""


class TransientSelector(Generic[T]):
    """Render ``items`` and return the user's selection or ``None``."""

    def __init__(
        self,
        title: str,
        items: Sequence[SelectorItem[T]],
        *,
        stdout: TextIO | None = None,
    ) -> None:
        self._title = title
        self._items = tuple(items)
        self._stdout = stdout if stdout is not None else sys.stdout
        self._index = 0
        self._selected: SelectorItem[T] | None = None

    async def run(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> SelectorItem[T] | None:
        if not self._items:
            return None
        if input is None and output is None and not can_enter_alternate_screen(
            self._stdout
        ):
            return None
        app = self._build_application(input=input, output=output)
        await app.run_async()
        return self._selected

    def _build_application(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        bindings = KeyBindings()

        @bindings.add(Keys.Down, eager=True)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            self._index = (self._index + 1) % len(self._items)
            event.app.invalidate()

        @bindings.add(Keys.Up, eager=True)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            self._index = (self._index - 1) % len(self._items)
            event.app.invalidate()

        @bindings.add(Keys.Enter, eager=True)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            self._selected = self._items[self._index]
            event.app.exit()

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            self._selected = None
            event.app.exit()

        def get_text():  # type: ignore[no-untyped-def]
            lines: list[tuple[str, str]] = [("class:title", f"{self._title}\n\n")]
            for index, item in enumerate(self._items):
                marker = "▶ " if index == self._index else "  "
                style = "class:selected" if index == self._index else "class:item"
                detail = f"  {item.detail}" if item.detail else ""
                lines.append((style, f"{marker}{item.label}{detail}\n"))
            lines.append(("class:footer", "\nEnter to select · Esc to cancel"))
            return FormattedText(lines)

        window = Window(content=FormattedTextControl(get_text))
        return Application(
            layout=Layout(window),
            full_screen=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )