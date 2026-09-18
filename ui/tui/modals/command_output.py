"""只读命令输出结果模态弹窗（``/status``, ``/usage``, ``/mcp`` 等）。"""

from __future__ import annotations

from typing import Any

from rich.console import RenderableType
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static


class CommandOutputModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", "", priority=True),
        Binding("q", "close", "", priority=False),
    ]

    DEFAULT_CSS = """
    CommandOutputModal { align: center middle; }
    CommandOutputModal > Vertical {
        width: 90;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    CommandOutputModal #command-body { max-height: 28; overflow: auto; }
    """

    def __init__(self, title: str, renderable: RenderableType) -> None:
        super().__init__()
        self._title = title
        self._renderable = renderable

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self._title, id="command-title")
            yield Static(self._renderable, id="command-body")

    def action_close(self) -> None:
        self.dismiss(None)


__all__ = ["CommandOutputModal"]
