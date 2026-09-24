from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static

from ...provider.config import ProviderConfigurationDraft
from ...provider.errors import ProviderConfigurationError


class ProviderConnectModal(ModalScreen[ProviderConfigurationDraft | None]):
    """Collects address and secret sequentially; model selection is a separate picker."""

    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    def __init__(self) -> None:
        super().__init__()
        self._base_url: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="provider-connect"):
            yield Label("连接模型供应商", id="provider-connect-title")
            yield Static("API 地址", id="provider-connect-step")
            yield Input(placeholder="https://provider.example.com/v1", id="provider-connect-input")
            yield Static("", id="provider-connect-error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        field = event.input
        error = self.query_one("#provider-connect-error", Static)
        if self._base_url is None:
            try:
                ProviderConfigurationDraft(value, "placeholder")
            except ProviderConfigurationError:
                error.update("API 地址无效")
                return
            self._base_url = value
            error.update("")
            self.query_one("#provider-connect-step", Static).update("API Key")
            field.value = ""
            field.placeholder = "输入 API Key"
            field.password = True
            return
        if not value:
            error.update("API Key 不能为空")
            return
        self.dismiss(ProviderConfigurationDraft(self._base_url, value))

    def action_cancel(self) -> None:
        self.dismiss(None)
