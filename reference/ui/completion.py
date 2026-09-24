from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rich.cells import cell_len
from rich.table import Table
from rich.text import Text
from textual.widgets import OptionList
from textual.widgets._option_list import Option
from textual.message import Message


class CompletionMode(StrEnum):
    SLASH = "slash"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class CompletionItem:
    mode: CompletionMode
    identity: str
    primary: str
    description: str
    payload: object | None = None


class CompletionOverlay(OptionList):
    """只拥有候选布局、高亮、滚动和选择，不参与业务评分。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.mode: CompletionMode | None = None
        self._items: dict[str, CompletionItem] = {}
        self._pending = False
        self._capacity: int | None = None

    class Dismissed(Message):
        def __init__(self, mode: CompletionMode | None) -> None:
            super().__init__()
            self.mode = mode

    @property
    def pending(self) -> bool:
        return self._pending

    def enter_mode(self, mode: CompletionMode, *, capacity: int | None = None) -> None:
        self._capacity = capacity or self._mode_capacity()
        self.mode = mode
        self._pending = False
        self.display = True
        self._fit_height()

    def replace_items(
        self,
        items: tuple[CompletionItem, ...],
        *,
        highlighted_identity: str | None = None,
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
            option_id = item.identity
            self._items[option_id] = item
            row = Table.grid(padding=(0, 2), expand=True)
            row.add_column(width=primary_width, no_wrap=True, overflow="ignore")
            row.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
            row.add_row(
                Text(item.primary, style="ui.label.agent", no_wrap=True),
                Text(item.description, style="ui.meta", no_wrap=True, overflow="ellipsis"),
            )
            options.append(Option(row, id=option_id))
        if not options and empty_label is not None:
            options.append(Option(Text(empty_label, style="ui.meta"), id="__completion_empty__", disabled=True))
        self.add_options(options)
        self.display = True
        if items:
            selected = next((index for index, item in enumerate(items) if item.identity == highlighted_identity), 0)
            self.highlighted = selected
        else:
            self.highlighted = None
        self._fit_height()

    def show_items(self, items: tuple[CompletionItem, ...], *, highlighted_identity: str | None = None) -> None:
        """兼容 slash 调用方；空结果代表 slash mode 结束。"""

        if not items:
            self.exit_mode()
            return
        self.enter_mode(items[0].mode)
        self.replace_items(items, highlighted_identity=highlighted_identity)

    def set_pending(self) -> None:
        """保留上一帧候选作视觉占位，但立即撤销其接受资格。"""

        if self.display:
            self._pending = True

    def item_for_option(self, option_id: str) -> CompletionItem | None:
        if self._pending:
            return None
        return self._items.get(option_id)

    def exit_mode(self) -> None:
        self.display = False
        self.mode = None
        self._items.clear()
        self._pending = False
        self._capacity = None
        self.clear_options()

    def hide(self) -> None:
        self.exit_mode()

    def on_resize(self) -> None:
        if self.display:
            self._capacity = self._mode_capacity()
            self._fit_height()

    def _fit_height(self) -> None:
        self.styles.height = self._capacity or self._mode_capacity()

    def _mode_capacity(self) -> int:
        available = self.screen.size.height if self.is_mounted else 24
        return min(8, max(1, available // 3))

    def handle_key(self, key: str) -> bool:
        if not self.display:
            return False
        if self._pending and key in {"tab", "enter"}:
            # 让 Composer/App 等待当前 generation；绝不能选择上一帧候选。
            return False
        if not self._items and key in {"tab", "enter"}:
            # 空状态只负责维持模式；Tab 不动作，Enter 交还 Composer 正常提交。
            return False
        if key == "up":
            self.action_cursor_up()
        elif key == "down":
            self.action_cursor_down()
        elif key in {"tab", "enter"}:
            self.action_select()
        elif key == "escape":
            dismissed_mode = self.mode
            self.exit_mode()
            self.post_message(self.Dismissed(dismissed_mode))
        else:
            return False
        return True
