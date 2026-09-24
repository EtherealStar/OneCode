"""权限请求确认模态弹窗。

它仅消费工具自身提供的 ``PermissionOption`` 选项（允许单次、允许本会话/目录、拒绝）。
按 Escape 键一律视为拒绝。绝不授予项目级作用域，也绝不写入项目规则。
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.permissions import render_permission_request_summary


class PermissionModal(ModalScreen[PermissionResponse | None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "deny", "", priority=True)]

    DEFAULT_CSS = """
    PermissionModal { align: center middle; }
    PermissionModal > Vertical {
        width: 80;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    PermissionModal Button { margin-top: 1; width: 100%; }
    PermissionModal #permission-summary { max-height: 16; overflow: auto; }
    """

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(
                f"工具 {self.request.descriptor.name} 请求授权", id="permission-title"
            )
            yield Static(
                render_permission_request_summary(self.request),
                id="permission-summary",
            )
            options = self.request.options or ()
            for option in options:
                yield Button(
                    option.label,
                    id=f"permission-option-{option.id}",
                    variant="primary" if option.action == "allow" else "error",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        option_id = button_id.removeprefix("permission-option-")
        for option in self.request.options:
            if option.id == option_id:
                self.dismiss(
                    PermissionResponse(action=option.action, scope=option.scope)
                )
                return

    def action_deny(self) -> None:
        self.dismiss(
            PermissionResponse(
                action="deny",
                scope="once",
                feedback="User denied the permission request.",
            )
        )


__all__ = ["PermissionModal"]
