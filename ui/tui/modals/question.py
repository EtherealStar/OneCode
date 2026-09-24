"""结构化用户提问模态弹窗。

每个屏幕显示一个问题；App 依序提问并组装生成 ``QuestionResponse``。
单选题使用 ``OptionList``；多选题使用带有显式提交按钮的 ``SelectionList``。
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, SelectionList, Static
from textual.widgets._option_list import Option

from services.questions.types import QuestionRequest


class QuestionModal(ModalScreen[tuple[str, ...] | None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    QuestionModal { align: center middle; }
    QuestionModal > Vertical {
        width: 80;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    QuestionModal #question-body { max-height: 6; }
    QuestionModal OptionList, QuestionModal SelectionList { height: auto; max-height: 12; }
    QuestionModal Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, request: QuestionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.request.header or "问题", id="question-title")
            yield Static(self.request.question, id="question-body")
            if self.request.multi_select:
                yield SelectionList[str](
                    *((option.label, option.label) for option in self.request.options),
                    id="question-select",
                )
                yield Button("提交", id="question-submit", variant="primary")
            else:
                yield OptionList(
                    *(
                        Option(option.label, id=str(index))
                        for index, option in enumerate(self.request.options)
                    ),
                    id="question-options",
                )
            yield Button("取消", id="question-cancel")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is None:
            return
        index = int(event.option.id)
        option = self.request.options[index]
        self.dismiss((option.label,))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "question-cancel":
            self.dismiss(None)
            return
        if event.button.id == "question-submit":
            selected = self.query_one(SelectionList).selected
            values = tuple(str(value) for value in selected)
            if not values:
                return
            self.dismiss(values)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["QuestionModal"]
