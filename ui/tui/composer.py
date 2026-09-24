"""OneCode TUI 编辑输入框控件。

一个精简的 :class:`~textual.widgets.TextArea` 子类，负责提交/换行/补全按键语义以及与补全浮层的交互握手。
仅发布意图消息，绝不执行命令、收集附件或更改运行时状态。
"""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from ui.tui.completion import CompletionOverlay


class Composer(TextArea):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "submit_text", "提交", show=False),
        Binding("ctrl+enter", "insert_newline", "换行", show=False),
        Binding("ctrl+j", "insert_newline", "换行", show=False),
        Binding("tab", "complete", "补全", show=False),
    ]

    DEFAULT_CSS = """
    Composer {
        height: auto;
        min-height: 3;
        max-height: 8;
        border: none;
        background: $surface;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class CancelRequested(Message):
        pass

    class CompletionRequested(Message):
        def __init__(self, text: str, cursor_offset: int) -> None:
            super().__init__()
            self.text = text
            self.cursor_offset = cursor_offset

    # --- 按键语义 ---------------------------------------------------------

    async def _on_key(self, event) -> None:
        overlay = self._overlay()
        if overlay is not None and overlay.handle_key(event.key):
            event.prevent_default()
            event.stop()
            return
        if event.key in {"ctrl+enter", "ctrl+j"}:
            event.prevent_default()
            event.stop()
            self.action_insert_newline()
            return
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            self.action_complete()
            return
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.action_submit_text()
            return
        if event.key == "ctrl+c":
            event.prevent_default()
            event.stop()
            self.post_message(self.CancelRequested())
            return
        # Escape 键由浮层/模态对话框层处理，而非输入框负责。
        await super()._on_key(event)

    def action_submit_text(self) -> None:
        if self.text.strip():
            self.post_message(self.Submitted(self.text))

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        self.post_message(self.CompletionRequested(self.text, self.cursor_offset()))

    # --- 辅助方法 ----------------------------------------------------------

    def cursor_offset(self) -> int:
        row, column = self.cursor_location
        lines = self.text.split("\n")
        offset = sum(len(line) + 1 for line in lines[:row])
        return min(offset + column, len(self.text))

    def clear_text(self) -> None:
        self.text = ""
        self.move_cursor((0, 0))

    def set_text(self, text: str, *, cursor: int | None = None) -> None:
        self.text = text
        offset = len(text) if cursor is None else min(cursor, len(text))
        self._move_to_offset(offset)

    def _move_to_offset(self, offset: int) -> None:
        text = self.text
        if offset <= 0:
            self.move_cursor((0, 0))
            return
        before = text[:offset]
        row = before.count("\n")
        column = offset - (before.rfind("\n") + 1)
        self.move_cursor((row, column))

    def _overlay(self) -> CompletionOverlay | None:
        try:
            return self.app.query_one(CompletionOverlay)
        except Exception:  # noqa: BLE001
            return None


__all__ = ["Composer"]
