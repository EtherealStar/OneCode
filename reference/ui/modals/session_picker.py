from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets._option_list import Option

from ...repository import SessionSummary


def format_last_updated(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "—"
    local_now = now or datetime.now().astimezone()
    if local_now.tzinfo is not None:
        local_value = value.replace(tzinfo=local_now.tzinfo) if value.tzinfo is None else value.astimezone(local_now.tzinfo)
    else:
        local_value = value.replace(tzinfo=None)
    elapsed = max(local_now - local_value, timedelta())
    if elapsed < timedelta(minutes=1):
        return "刚刚"
    if elapsed < timedelta(hours=1):
        return f"{int(elapsed.total_seconds() // 60)} 分钟前"
    if local_value.date() == local_now.date():
        return local_value.strftime("%H:%M")
    if local_value.date() == (local_now - timedelta(days=1)).date():
        return f"昨天 {local_value:%H:%M}"
    if local_value.year == local_now.year:
        return local_value.strftime("%m-%d")
    return local_value.strftime("%Y-%m-%d")


def _two_column_row(left: Text | str, right: Text | str) -> Table:
    row = Table.grid(expand=True, padding=(0, 1))
    row.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    row.add_column(width=18, justify="right", no_wrap=True)
    row.add_row(left, right)
    return row


class SessionPickerModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    def __init__(self, sessions: Iterable[SessionSummary]) -> None:
        super().__init__()
        self.sessions = tuple(sessions)

    def compose(self) -> ComposeResult:
        options = []
        for item in self.sessions:
            title = Text(item.name, style="ui.text" if item.openable else "ui.meta")
            if not item.openable:
                title.append(" · 不可用", style="ui.error")
            options.append(
                Option(
                    _two_column_row(title, Text(format_last_updated(item.last_user_input_at), style="ui.meta")),
                    id=item.session_id,
                    disabled=not item.openable,
                )
            )
        with Vertical(id="session-picker"):
            yield Label("选择历史会话", id="session-picker-title")
            yield Static(_two_column_row("会话标题", "Last updated"), id="session-picker-header")
            yield OptionList(*options, id="sessions")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)
