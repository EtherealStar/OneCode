"""OneCode TUI 状态栏控件。

左侧：产品/工作区/模型。右侧：运行状态，在轮次处于活跃状态时显示旋转动画帧。
仅渲染投影出来的既定事实。
"""

from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Static

from ui.tui.renderers.status import (
    RunState,
    render_run_state,
    render_status_left,
)


class StatusBar(Horizontal):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    StatusBar Static { height: 1; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._run_state: RunState | None = None
        self._frame = 0
        self._timer = None
        self._workspace_root = ""
        self._model: str | None = None

    def compose(self):
        yield Static(id="status-left")
        yield Static(id="status-right")

    def update_status(self, workspace_root: str, model: str | None) -> None:
        self._workspace_root = workspace_root
        self._model = model
        self._render_left()

    def on_resize(self) -> None:
        self._render_left()

    def update_run_state(self, state: RunState | None) -> None:
        if state == self._run_state:
            return
        self._run_state = state
        self._render_state()
        running = state is not None and state.kind == "running"
        if running and self._timer is None:
            self._timer = self.set_interval(1 / 8, self._tick)
        elif not running and self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _tick(self) -> None:
        self._frame += 1
        self._render_state()

    def _render_state(self) -> None:
        if not self.is_mounted:
            return
        self.query_one("#status-right", Static).update(
            render_run_state(self._run_state, self._frame)
        )
        self._render_left()

    def _render_left(self) -> None:
        if not self.is_mounted:
            return
        right = self.query_one("#status-right", Static)
        width = max(1, self.size.width - right.outer_size.width - 2)
        self.query_one("#status-left", Static).update(
            render_status_left(
                self._workspace_root, self._model, max_width=width
            )
        )


__all__ = ["StatusBar"]
