"""会话恢复选择器模态弹窗。"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static
from textual.widgets._option_list import Option

from application.sessions import SessionSummary


def _format_updated(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is not None:
        value = value.astimezone()
    return value.strftime("%Y-%m-%d %H:%M")


class SessionPickerModal(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    SessionPickerModal { align: center middle; }
    SessionPickerModal > Vertical {
        width: 88;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    SessionPickerModal OptionList { height: auto; max-height: 18; }
    """

    def __init__(self, sessions: tuple[SessionSummary, ...]) -> None:
        super().__init__()
        self.sessions = sessions

    def compose(self) -> ComposeResult:
        options = []
        for summary in self.sessions:
            row = Text()
            row.append(summary.title or summary.session_id, style="ui.label.agent")
            row.append(
                f"  · {summary.message_count} 条 · {_format_updated(summary.updated_at)}",
                style="ui.meta",
            )
            options.append(Option(row, id=summary.session_id))
        with Vertical():
            yield Label("选择历史会话", id="session-picker-title")
            if not options:
                yield Static("没有可恢复的会话。", id="session-picker-empty")
            else:
                yield OptionList(*options, id="session-options")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None:
            self.dismiss(str(event.option.id))

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["SessionPickerModal"]
