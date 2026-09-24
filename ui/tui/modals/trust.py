"""MCP 信任确认模态弹窗。

在系统启动阶段当项目 stdio MCP 服务器尚未获得信任时展示。
回答结果为 ``"trust"`` 或 ``"skip"``，并同步返回给运行时装配 worker，
以确保启动过程与 UI 界面绝不死锁。
"""

from __future__ import annotations

from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class McpTrustModal(ModalScreen[str | None]):
    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "skip", "", priority=True)]

    DEFAULT_CSS = """
    McpTrustModal { align: center middle; }
    McpTrustModal > Vertical {
        width: 80;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    McpTrustModal Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, request: Any) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        request = self.request
        with Vertical():
            yield Label("信任项目 MCP 服务器", id="trust-title")
            yield Static(
                "\n".join(
                    [
                        f"server: {getattr(request, 'server_name', '')}",
                        f"command: {getattr(request, 'command', '')}",
                        f"args: {getattr(request, 'args', '')}",
                        f"cwd: {getattr(request, 'cwd', '')}",
                        f"explicit env keys: {getattr(request, 'explicit_env_keys', '(none)')}",
                        f"base env keys: {getattr(request, 'base_env_keys', '(none)')}",
                    ]
                ),
                id="trust-detail",
            )
            yield Button("信任", id="trust-accept", variant="primary")
            yield Button("跳过", id="trust-skip")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "trust-accept":
            self.dismiss("trust")
        elif event.button.id == "trust-skip":
            self.dismiss("skip")

    def action_skip(self) -> None:
        self.dismiss("skip")


__all__ = ["McpTrustModal"]
