from __future__ import annotations

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static


class ModelPickerModal(ModalScreen[str | None]):
    """展示 App 持有的缓存目录，并把显式刷新意图发回 App。"""

    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    class RefreshRequested(Message):
        def __init__(self, picker: ModelPickerModal) -> None:
            super().__init__()
            self.picker = picker

    def __init__(self, models: Iterable[str] | None, *, current: str | None) -> None:
        super().__init__()
        self._models = tuple(models) if models is not None else None
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="model-picker"):
            yield Label("选择模型", id="model-picker-title")
            yield Static("", id="model-picker-state")
            yield OptionList(id="models")
            yield Input(value=self._current or "", placeholder="输入模型名", id="manual-model")
            yield Button("获取模型列表", id="refresh-models", variant="primary")

    def on_mount(self) -> None:
        self._render_models()

    def show_loading(self) -> None:
        self.query_one("#model-picker-state", Static).update("正在获取模型列表…")
        self.query_one("#refresh-models", Button).disabled = True

    def show_models(self, models: Iterable[str]) -> None:
        self._models = tuple(models)
        self.query_one("#refresh-models", Button).disabled = False
        self.query_one("#model-picker-state", Static).update(
            "" if self._models else "Provider 未返回可用模型"
        )
        self._render_models()

    def show_error(self, error: str) -> None:
        self.query_one("#model-picker-state", Static).update(error)
        self.query_one("#refresh-models", Button).disabled = False

    def _render_models(self) -> None:
        option_list = self.query_one("#models", OptionList)
        option_list.clear_options()
        models = list(self._models or ())
        if self._current and self._current not in models:
            models.insert(0, self._current)
        option_list.add_options(models)
        option_list.display = bool(models)
        state = self.query_one("#model-picker-state", Static)
        if self._models is None:
            state.update("尚未获取模型列表")
        elif not models:
            state.update("Provider 未返回可用模型")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(str(event.option.prompt))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "manual-model" and event.value.strip():
            self.dismiss(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-models":
            self.show_loading()
            self.post_message(self.RefreshRequested(self))

    def action_cancel(self) -> None:
        self.dismiss(None)
