"""Provider connection wizard screens.

The App orchestrates the steps (provider → credentials → model); these screens
only collect values. Saving ``.env`` and reloading the runtime stay in the App
through ``ui.cli.connect`` and ``SessionController.reload_model_config``.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets._option_list import Option

from infrastructure.providers.catalog import ProviderDefinition
from infrastructure.providers.connection import ConnectOption


class ProviderPickerModal(ModalScreen[ConnectOption | None]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    ProviderPickerModal { align: center middle; }
    ProviderPickerModal > Vertical {
        width: 72; height: auto; max-height: 80%;
        padding: 1 2; border: solid $accent; background: $surface;
    }
    ProviderPickerModal OptionList { height: auto; max-height: 18; }
    """

    def __init__(self, options: tuple[ConnectOption, ...]) -> None:
        super().__init__()
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("选择模型供应商", id="connect-provider-title")
            yield OptionList(
                *(
                    Option(
                        f"{option.display_name} ({option.provider_id})",
                        id=option.provider_id,
                    )
                    for option in self.options
                ),
                id="connect-providers",
            )

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        for option in self.options:
            if option.provider_id == event.option.id:
                self.dismiss(option)
                return

    def action_cancel(self) -> None:
        self.dismiss(None)


@dataclass(frozen=True)
class CredentialResult:
    base_url: str | None
    api_key: str


class CredentialModal(ModalScreen[CredentialResult | None]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    CredentialModal { align: center middle; }
    CredentialModal > Vertical {
        width: 72; height: auto;
        padding: 1 2; border: solid $accent; background: $surface;
    }
    CredentialModal Input { margin-bottom: 1; }
    """

    def __init__(
        self,
        provider: ProviderDefinition,
        *,
        existing_key: str | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.existing_key = existing_key
        self._base_url: str | None = None

    def compose(self) -> ComposeResult:
        provider = self.provider
        with Vertical():
            yield Label(f"配置 {provider.display_name}", id="credential-title")
            if provider.requires_base_url:
                yield Static("Base URL", id="credential-base-label")
                yield Input(
                    value=provider.base_url or "",
                    placeholder="https://provider.example.com/v1",
                    id="credential-base-url",
                )
            if provider.api_key_required:
                hint = "留空保留现有 Key" if self.existing_key else "API Key"
                yield Static(hint, id="credential-key-label")
                yield Input(placeholder=hint, password=True, id="credential-api-key")
            yield Button("继续", id="credential-submit", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "credential-base-url":
            value = event.value.strip()
            if not value:
                return
            self._base_url = value
            key_input = self.query_one_optional("#credential-api-key", Input)
            if key_input is not None:
                key_input.focus()
            return
        if event.input.id == "credential-api-key":
            self._finish(event.value.strip())
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "credential-submit":
            self._finish_current()

    def _finish_current(self) -> None:
        key_input = self.query_one_optional("#credential-api-key", Input)
        self._finish(key_input.value.strip() if key_input is not None else "")

    def _finish(self, key: str) -> None:
        provider = self.provider
        base_url = self._base_url or (provider.base_url or None)
        if provider.requires_base_url and not base_url:
            return
        if provider.api_key_required:
            if not key and not self.existing_key:
                return
            api_key = key or (self.existing_key or "")
        else:
            api_key = ""
        self.dismiss(CredentialResult(base_url=base_url, api_key=api_key))

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelPickerModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    ModelPickerModal { align: center middle; }
    ModelPickerModal > Vertical {
        width: 72; height: auto; max-height: 80%;
        padding: 1 2; border: solid $accent; background: $surface;
    }
    ModelPickerModal OptionList { height: auto; max-height: 16; }
    """

    def __init__(self, models: tuple[str, ...]) -> None:
        super().__init__()
        self.models = models

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("选择模型", id="model-picker-title")
            if self.models:
                yield OptionList(
                    *(Option(model, id=model) for model in self.models),
                    id="model-options",
                )
            yield Input(placeholder="或手动输入模型名称", id="model-manual")

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        if event.option.id is not None:
            self.dismiss(str(event.option.id))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        if value:
            self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = [
    "CredentialModal",
    "CredentialResult",
    "ModelPickerModal",
    "ProviderPickerModal",
]
