"""ConversationView: TUI 会话显示模块。

该视图接收只读的会话投影与语义变更集。封装了虚拟布局、滚动锚定、
Markdown 缓存、详情展开以及刷新调度机制。它不运行 Agent、不读取记录文件，也不绑定按键。

``update`` 仅安排刷新计划；会话投影本身已经是权威状态。
携带空脏标记集合的附属状态变更（队列/状态/用量/运行）仍会被正常刷出，不会被调度器吞没。
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container

from ui.tui.conversation.refresh_scheduler import DirtyIds, UiRefreshScheduler
from ui.tui.conversation.viewport import (
    DEFAULT_OVERSCAN,
    DetailRequested,
    MessageViewport,
    MessageWidget,
    NewContentButton,
)
from ui.tui.projection import ConversationProjection
from ui.tui.projection_types import ViewChange
from ui.tui.renderers.tool import ToolPresentationRegistry


class ConversationView(Container):
    """全屏会话模块：虚拟视口与刷新调度。"""

    DEFAULT_CSS = """
    ConversationView {
        width: 100%;
        height: 1fr;
        background: $background;
    }
    """

    def __init__(
        self,
        *,
        tool_presentations: ToolPresentationRegistry | None = None,
        scheduler_delay: float = 0.04,
        overscan: int = DEFAULT_OVERSCAN,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._tool_presentations = tool_presentations
        self._overscan = overscan
        self._projection: ConversationProjection | None = None
        self._scheduler = UiRefreshScheduler(
            self.set_timer, self._flush, delay=scheduler_delay
        )
        self._viewport: MessageViewport | None = None

    def compose(self) -> ComposeResult:
        yield MessageViewport(
            tool_presentations=self._tool_presentations,
            overscan=self._overscan,
            id="message-viewport",
        )
        yield NewContentButton("↓ 新内容", id="new-content")

    def on_mount(self) -> None:
        self._viewport = self.query_one(MessageViewport)

    def on_unmount(self) -> None:
        self.close()

    @property
    def viewport(self) -> MessageViewport:
        if self._viewport is None:
            self._viewport = self.query_one(MessageViewport)
        return self._viewport

    def update(self, projection: ConversationProjection, change: ViewChange) -> None:
        self._projection = projection
        if change.empty and not change.resync_required:
            return
        structural = (
            change.reset or change.structure_changed or bool(change.deleted_ids)
        )
        auxiliary = (
            change.queue_changed
            or change.status_changed
            or change.interaction_changed
            or change.usage_changed
            or change.run_changed
        )
        dirty: DirtyIds = set(change.updated_ids)
        self._scheduler.request(
            dirty,
            structural=structural or auxiliary,
            immediate=change.immediate or change.resync_required,
        )

    def refresh_now(self) -> None:
        """立即刷出所有待处理的刷新任务（供测试与 App 回调使用）。"""

        self._scheduler.flush_now()

    def close(self) -> None:
        self._scheduler.close()

    def _flush(self, dirty: DirtyIds, structural: bool) -> None:
        if self._projection is None:
            return
        viewport = self.viewport
        viewport.sync_projection(self._projection, dirty, structural=structural)


__all__ = [
    "ConversationView",
    "DetailRequested",
    "MessageViewport",
    "MessageWidget",
    "NewContentButton",
]
