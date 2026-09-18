"""Completion overlay for the OneCode TUI.

The overlay owns only candidate layout, highlight, scrolling, and selection.
It never scores or fetches candidates: the App computes them from the OneCode
command registry and the workspace attachment resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.cells import cell_len
from rich.table import Table
from rich.text import Text
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets._option_list import Option

CompletionMode = Literal["command", "file"]


@dataclass(frozen=True, slots=True)
class CompletionItem:
    mode: CompletionMode
    identity: str
    primary: str
    description: str = ""
    #: Text substituted for the active query when the item is accepted.
    replacement: str = ""


class CompletionOverlay(OptionList):
    """Selectable candidate list shared by slash and ``@`` completion."""

    DEFAULT_CSS = """
    CompletionOverlay {
        display: none;
        height: auto;
        max-height: 8;
        border: none;
        background: $surface;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mode: CompletionMode | None = None
        self._items: dict[str, CompletionItem] = {}
        self._pending = False

    class Dismissed(Message):
        def __init__(self, mode: CompletionMode | None) -> None:
            super().__init__()
            self.mode = mode

    @property
    def pending(self) -> bool:
        return self._pending

    def enter_mode(self, mode: CompletionMode) -> None:
        self.mode = mode
        self._pending = False
        self.display = True

    def replace_items(
        self,
        items: tuple[CompletionItem, ...],
        *,
        empty_label: str | None = None,
    ) -> None:
        if self.mode is None:
            if not items:
                return
            self.enter_mode(items[0].mode)
        if any(item.mode is not self.mode for item in items):
            raise ValueError("Completion items must match the active mode.")
        self.clear_options()
        self._items.clear()
        self._pending = False
        primary_width = max((cell_len(item.primary) for item in items), default=0)
        options: list[Option] = []
        for item in items:
            self._items[item.identity] = item
            row = Table.grid(padding=(0, 2), expand=True)
            row.add_column(width=primary_width, no_wrap=True, overflow="ignore")
            row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
            row.add_row(
                Text(item.primary, style="ui.label.agent", no_wrap=True),
                Text(
                    item.description,
                    style="ui.meta",
                    no_wrap=True,
                    overflow="ellipsis",
                ),
            )
            options.append(Option(row, id=item.identity))
        if not options and empty_label is not None:
            options.append(
                Option(Text(empty_label, style="ui.meta"), id="__empty__", disabled=True)
            )
        self.add_options(options)
        self.display = True
        self.highlighted = 0 if items else None

    def set_pending(self) -> None:
        """Keep the last frame visible while revoking its accept eligibility."""

        if self.display:
            self._pending = True

    def item_for_option(self, option_id: str) -> CompletionItem | None:
        if self._pending:
            return None
        return self._items.get(option_id)

    def selected_item(self) -> CompletionItem | None:
        if self._pending or self.highlighted is None:
            return None
        option = self.get_option_at_index(self.highlighted)
        if option is None or option.id is None:
            return None
        return self.item_for_option(str(option.id))

    def exit_mode(self) -> None:
        self.display = False
        self.mode = None
        self._items.clear()
        self._pending = False
        self.clear_options()

    def hide(self) -> None:
        self.exit_mode()

    def handle_key(self, key: str) -> bool:
        if not self.display:
            return False
        if key in {"up", "down"}:
            if key == "up":
                self.action_cursor_up()
            else:
                self.action_cursor_down()
            return True
        if key in {"tab", "enter"}:
            if self._pending:
                # Never accept a stale frame; let the App wait for the
                # current generation to arrive.
                return False
            if not self._items:
                # Empty slash result: Enter falls through to submit, Tab no-ops.
                return key == "tab"
            self.action_select()
            return True
        if key == "escape":
            dismissed = self.mode
            self.exit_mode()
            self.post_message(self.Dismissed(dismissed))
            return True
        return False


__all__ = ["CompletionItem", "CompletionMode", "CompletionOverlay"]
