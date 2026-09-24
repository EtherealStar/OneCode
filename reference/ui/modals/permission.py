from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ...tools.authorization import PermissionDecision, PermissionRequest


class PermissionModal(ModalScreen[PermissionDecision]):
    """只展示规范化目标，不展示 secret、上传 URL 或原始 arguments。"""

    DEFAULT_CSS = """
    PermissionModal { align: center middle; }
    PermissionModal > Vertical { width: 72; height: auto; padding: 1 2; border: solid $accent; background: $surface; }
    PermissionModal Button { margin-top: 1; width: 100%; }
    """
    BINDINGS = [Binding("escape", "deny", "", priority=True)]

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        targets = "\n".join(
            f"- {target.kind}/{target.capability}/{target.scope}: {target.value}"
            for target in self.request.targets
        )
        with Vertical():
            yield Label(f"工具 {self.request.tool_name} 请求访问：")
            yield Static(targets)
            yield Button("拒绝", id="deny")
            yield Button("仅本次允许", id="allow_once", variant="primary")
            yield Button("本 Session 允许", id="allow_session")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(PermissionDecision(event.button.id))

    def action_deny(self) -> None:
        self.dismiss(PermissionDecision.DENY)
