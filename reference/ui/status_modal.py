from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from rich.cells import cell_len
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, Static

from ..usage import SessionUsage


NOT_CONFIGURED = "Not configured"


@dataclass(frozen=True, slots=True)
class StatusViewState:
    session_name: str | None
    session_id: UUID | None
    workspace: str
    base_url: str | None
    model: str | None
    usage: SessionUsage


def format_usage(usage: SessionUsage) -> str:
    return (
        f"{usage.input_tokens:,} input, {usage.output_tokens:,} output, "
        f"{usage.cache_read_tokens:,} cache read, {usage.cache_write_tokens:,} cache write"
    )


def middle_ellipsis(value: str, max_cells: int) -> str:
    """按终端 cell 宽度从中间省略，同时保留可辨认的首尾。"""

    if max_cells <= 0:
        return ""
    if cell_len(value) <= max_cells:
        return value
    if max_cells == 1:
        return "…"
    remaining = max_cells - 1
    left_budget = (remaining + 1) // 2
    right_budget = remaining // 2

    def prefix(cells: int) -> str:
        result = ""
        for character in value:
            if cell_len(result + character) > cells:
                break
            result += character
        return result

    def suffix(cells: int) -> str:
        result = ""
        for character in reversed(value):
            if cell_len(character + result) > cells:
                break
            result = character + result
        return result

    return f"{prefix(left_budget)}…{suffix(right_budget)}"


class StatusModal(ModalScreen[None]):
    BINDINGS = [Binding("escape", "close", "", priority=True)]

    def __init__(self, state: StatusViewState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical(id="status-modal"):
            yield Label("Status", id="status-title")
            for key, label in (
                ("session-name", "Session name"),
                ("session-id", "Session ID"),
                ("workspace", "Workspace"),
                ("base-url", "Base URL"),
                ("model", "Model"),
            ):
                with Horizontal(classes="status-row"):
                    yield Static(label, classes="status-label")
                    yield Static("", id=f"status-{key}", classes="status-value")
            yield Static("", id="status-gap")
            with Horizontal(classes="status-row"):
                yield Static("Usage", classes="status-label")
                yield Static("", id="status-usage", classes="status-value")

    def on_mount(self) -> None:
        self._render_state()

    def on_resize(self) -> None:
        self._render_state()

    def update_usage(self, usage: SessionUsage) -> None:
        self.state = replace(self.state, usage=usage)
        if self.is_mounted:
            self.query_one("#status-usage", Static).update(format_usage(usage))

    def _render_state(self) -> None:
        if not self.is_mounted:
            return
        value_width = max(12, self.size.width - 22)
        values = {
            "session-name": self.state.session_name or NOT_CONFIGURED,
            "session-id": str(self.state.session_id) if self.state.session_id else NOT_CONFIGURED,
            "workspace": middle_ellipsis(self.state.workspace, value_width),
            "base-url": middle_ellipsis(self.state.base_url, value_width) if self.state.base_url else NOT_CONFIGURED,
            "model": self.state.model or NOT_CONFIGURED,
        }
        for key, value in values.items():
            self.query_one(f"#status-{key}", Static).update(value)
        self.query_one("#status-usage", Static).update(format_usage(self.state.usage))

    def action_close(self) -> None:
        self.dismiss(None)
