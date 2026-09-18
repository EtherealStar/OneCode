"""Plan approval modal.

Rendered when the ``exit_plan_mode`` tool reports ``awaiting_approval``. The
decision is returned to the App, which drives the existing ``/plan approve`` or
``/plan reject`` business so plan-mode transitions stay owned by OneCode.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static


class PlanApprovalModal(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "", priority=True)]

    DEFAULT_CSS = """
    PlanApprovalModal { align: center middle; }
    PlanApprovalModal > Vertical {
        width: 88;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: solid $accent;
        background: $surface;
    }
    PlanApprovalModal #plan-body { max-height: 20; overflow: auto; }
    PlanApprovalModal Button { margin-top: 1; width: 100%; }
    """

    def __init__(self, *, path: str, content: str, summary: str = "") -> None:
        super().__init__()
        self._path = path
        self._content = content
        self._summary = summary

    def compose(self) -> ComposeResult:
        body = self._content.strip() or "(空计划文件)"
        with Vertical():
            yield Label("计划审批", id="plan-title")
            if self._summary:
                yield Static(self._summary, id="plan-summary")
            yield Static(f"文件: {self._path}", id="plan-path")
            yield Static(body, id="plan-body")
            yield Button("批准并退出计划模式", id="plan-approve", variant="primary")
            yield Button("拒绝", id="plan-reject", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "plan-approve":
            self.dismiss("approve")
        elif event.button.id == "plan-reject":
            self.dismiss("reject")

    def action_cancel(self) -> None:
        self.dismiss(None)


__all__ = ["PlanApprovalModal"]
