"""ConversationView: the TUI conversation display module.

The view receives a read-only projection and a semantic change set. It hides
virtual layout, scroll anchoring, Markdown caching, detail expansion, and the
refresh scheduler. It does not run the agent, read transcripts, or bind keys.

``update`` only schedules a refresh; the projection is already authoritative.
Auxiliary state changes with an empty dirty set (queue/status/usage/run) still
flush, so they cannot be swallowed by the scheduler.
"""

from __future__ import annotations

from typing import Any

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
    """Full-screen conversation module: virtual viewport + refresh scheduling."""

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

    def update(
        self, projection: ConversationProjection, change: ViewChange
    ) -> None:
        self._projection = projection
        if change.empty and not change.resync_required:
            return
        structural = (
            change.reset
            or change.structure_changed
            or bool(change.deleted_ids)
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
        """Flush any pending refresh immediately (tests, App callbacks)."""

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
