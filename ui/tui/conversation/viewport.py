"""虚拟消息视口。

仅将可见消息加上预加载区域挂载为控件。屏幕外的内容通过两个占位控件利用
:class:`VirtualLayoutIndex` 来模拟呈现。布局键采用稳定的消息标识符，
因此工具乱序执行完成绝不会扰乱时间线的顺序，仅有变更的控件会被更新。
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, cast

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from ui.tui.conversation.layout_index import VirtualLayoutIndex
from ui.tui.projection import ConversationProjection
from ui.tui.projection_types import PART_TOOL, UiMessage, UiPart
from ui.tui.renderers.message import MarkdownCaches, render_message
from ui.tui.renderers.tool import (
    ToolPresentationRegistry,
    build_tool_presentation_registry,
)

LayoutKey = tuple[str, Hashable]

#: 基准预加载行数：在视口上下各额外挂载指定数量的消息。
DEFAULT_OVERSCAN = 8


class DetailRequested(Message):
    """当展开的工具需要加载外部详细数据时发出该消息。"""

    def __init__(
        self,
        message_id: Hashable,
        tool_key: str,
        detail_ref: Any,
    ) -> None:
        super().__init__()
        self.message_id = message_id
        self.tool_key = tool_key
        self.detail_ref = detail_ref


class _Spacer(Static):
    can_focus = False


class MessageWidget(Static):
    """由 ``message_id`` 唯一标识的稳定消息控件。"""

    def __init__(
        self,
        message: UiMessage,
        caches: MarkdownCaches,
        tool_presentations: ToolPresentationRegistry,
        *,
        expanded: bool = False,
    ) -> None:
        super().__init__(classes=f"message message-{message.lifecycle}")
        self.message_id = message.message_id
        self._caches = caches
        self._tool_presentations = tool_presentations
        self._expanded = expanded
        self._message = message
        self.can_focus = False
        self.update_message(message, expanded)

    @property
    def message(self) -> UiMessage:
        return self._message

    @property
    def expanded(self) -> bool:
        return self._expanded

    def update_message(self, message: UiMessage, expanded: bool) -> None:
        self._message = message
        self._expanded = expanded
        self.set_classes(f"message message-{message.lifecycle}")
        self.update(
            render_message(
                message,
                details_expanded=expanded,
                md_caches=self._caches,
                tool_presentations=self._tool_presentations,
            )
        )

    def missing_detail_requests(self) -> tuple[UiPart, ...]:
        """展开时需要拉取外部详情的工具分片元组。"""

        if not self._expanded:
            return ()
        pending: list[UiPart] = []
        for part in self._message.parts:
            if part.kind != PART_TOOL:
                continue
            if part.detail_ref is None or part.detail_loaded:
                continue
            if part.detail_error is not None:
                continue
            pending.append(part)
        return tuple(pending)

    def on_click(self) -> None:
        if not any(part.kind == PART_TOOL for part in self._message.parts):
            return
        if isinstance(self.parent, MessageViewport):
            self.parent.toggle_message_expanded(self.message_id)


class NewContentButton(Static):
    """当用户从底部向上滚动离开时显示的新内容提示按钮。"""

    def on_click(self) -> None:
        if self.parent is None:
            return
        try:
            self.parent.query_one(MessageViewport).jump_to_latest()
        except NoMatches:
            pass


class MessageViewport(VerticalScroll):
    """覆盖投影消息的全量文档虚拟滚动视口。"""

    def __init__(
        self,
        *args: Any,
        tool_presentations: ToolPresentationRegistry | None = None,
        overscan: int = DEFAULT_OVERSCAN,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._tool_presentations = (
            tool_presentations or build_tool_presentation_registry()
        )
        self._overscan = overscan
        self._md_caches: MarkdownCaches = {}
        self._projection: ConversationProjection | None = None
        self._documents: list[tuple[LayoutKey, UiMessage]] = []
        self._index = VirtualLayoutIndex(default_height=2)
        self._widgets: dict[LayoutKey, MessageWidget] = {}
        self._message_expanded: dict[Hashable, bool] = {}
        self._detail_default = False
        self._visible_range = (0, 0)
        self._message_count = 0
        self._resize_anchor: tuple[Hashable, int] | None = None
        self._resize_timer = None
        self._last_width = 0
        self._restore_anchor: tuple[Hashable, int] | None = None
        self._flush_generation = 0
        self._auto_scroll_generation = 0
        self._issuing_internal_scroll = False
        self._follow_bottom = True
        self.requested_updates = 0
        self.flush_count = 0
        self.dirty_widget_count = 0
        self.structural_flush_count = 0

    def compose(self) -> ComposeResult:
        yield _Spacer(id="virtual-top-spacer")
        yield _Spacer(id="virtual-bottom-spacer")

    # --- 测试与外层视图使用的读取属性 -----------------------------------

    @property
    def visible_range(self) -> tuple[int, int]:
        return self._visible_range

    @property
    def document_count(self) -> int:
        return len(self._documents)

    @property
    def mounted_message_count(self) -> int:
        return len(self._widgets)

    def widget_for(self, message_id: Hashable) -> MessageWidget | None:
        return self._widgets.get(("message", message_id))

    # --- 投影同步 ---------------------------------------------------------

    def refresh_projection(self, projection: ConversationProjection) -> None:
        self.sync_projection(
            projection,
            {message.message_id for message in projection.messages},
            structural=True,
        )

    def sync_projection(
        self,
        projection: ConversationProjection,
        dirty_message_ids: set[Hashable],
        *,
        structural: bool = False,
    ) -> None:
        self.requested_updates += 1
        self.flush_count += 1
        self._flush_generation += 1
        generation = self._flush_generation
        self._projection = projection
        at_bottom = self._follow_bottom and self._at_bottom()
        anchor = None if at_bottom else self._index.locate(int(self.scroll_y))
        self._restore_anchor = anchor
        old_keys = tuple(key for key, _ in self._documents)
        self._documents = self._build_documents(projection)
        new_keys = tuple(key for key, _ in self._documents)
        structural = structural or old_keys != new_keys
        if structural:
            self.structural_flush_count += 1
            self._index.sync(new_keys)
        self._message_count = len(projection.messages)
        self._prune_state(set(new_keys))

        start, end = self._desired_range()
        if structural or (start, end) != self._visible_range:
            self._mount_range(start, end)
        for key, item in self._documents[start:end]:
            if item.message_id in dirty_message_ids:
                widget = self._widgets.get(key)
                if widget is not None:
                    widget.update_message(
                        item,
                        self._message_expanded.get(
                            item.message_id, self._detail_default
                        ),
                    )
                    self.dirty_widget_count += 1
        self._update_spacers(start, end)
        auto_scroll_generation = self._auto_scroll_generation
        self.call_after_refresh(
            self._complete_projection_flush,
            generation,
            auto_scroll_generation,
            anchor,
            at_bottom,
        )
        self._sync_new_content_button()

    def toggle_message_expanded(
        self, message_id: Hashable, expanded: bool | None = None
    ) -> bool:
        current = self._message_expanded.get(message_id, self._detail_default)
        target = (not current) if expanded is None else expanded
        self._message_expanded[message_id] = target
        key = ("message", message_id)
        widget = self._widgets.get(key)
        item = self._document_item(key)
        if isinstance(widget, MessageWidget) and isinstance(item, UiMessage):
            anchor = (
                None if self._at_bottom() else self._index.locate(int(self.scroll_y))
            )
            widget.update_message(item, target)
            self.call_after_refresh(
                self._measure_and_restore,
                anchor,
                self._at_bottom(),
                self._auto_scroll_generation,
            )
            if target:
                self._request_missing_details(item)
        return target

    def set_message_expanded(self, message_id: Hashable, expanded: bool) -> None:
        self.toggle_message_expanded(message_id, expanded)

    def set_all_details_expanded(self, expanded: bool) -> None:
        self._detail_default = expanded
        self._message_expanded = {
            message.message_id: expanded
            for message in (
                self._projection.messages if self._projection is not None else ()
            )
        }
        at_bottom = self._follow_bottom and self._at_bottom()
        if not at_bottom:
            self._invalidate_scroll_intent()
        anchor = None if at_bottom else self._index.locate(int(self.scroll_y))
        for key, widget in self._widgets.items():
            item = self._document_item(key)
            if isinstance(item, UiMessage):
                widget.update_message(item, expanded)
        if expanded:
            for _, item in self._documents:
                self._request_missing_details(item)
        self.call_after_refresh(
            self._measure_and_restore,
            anchor,
            at_bottom,
            self._auto_scroll_generation,
        )

    def _request_missing_details(self, message: UiMessage) -> None:
        key = ("message", message.message_id)
        widget = self._widgets.get(key)
        if widget is None:
            return
        for part in widget.missing_detail_requests():
            self.post_message(
                DetailRequested(
                    message.message_id, part.tool_call_id or "", part.detail_ref
                )
            )

    def jump_to_latest(self) -> None:
        self._follow_bottom = True
        self._auto_scroll_generation += 1
        generation = self._auto_scroll_generation
        tail_y = max(0, self._index.total_height - max(1, self.size.height))
        start, end = self._index.visible_range(
            tail_y, max(1, self.size.height), overscan=self._overscan
        )
        self._mount_range(start, end)
        self._update_spacers(start, end)
        self.call_after_refresh(self._complete_jump_to_latest, generation)
        self._sync_new_content_button()

    # --- 滚动重载与监听 ---------------------------------------------------

    def scroll_to(self, *args: Any, **kwargs: Any) -> None:
        if not self._issuing_internal_scroll:
            self._invalidate_scroll_intent()
        super().scroll_to(*args, **kwargs)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if not self._issuing_internal_scroll and int(old_value) != int(new_value):
            self._invalidate_scroll_intent()
            self._follow_bottom = new_value >= max(
                0, self._index.total_height - self.size.height - 1
            )
        if (
            int(old_value) != int(new_value)
            and self._restore_anchor is None
            and self._resize_anchor is None
        ):
            self.call_later(self._sync_visible_range, self._auto_scroll_generation)
        self._sync_new_content_button()

    def on_resize(self) -> None:
        self._reposition_new_content_button()
        width_changed = self.size.width != self._last_width
        self._last_width = self.size.width
        if self._projection is not None and self._documents and width_changed:
            if self._resize_anchor is None:
                self._resize_anchor = self._index.locate(int(self.scroll_y))
            self.call_later(self._sync_visible_range, self._auto_scroll_generation)
            self.call_after_refresh(self._finish_resize)

    # --- 内部实现方法 -----------------------------------------------------

    def _document_item(self, key: LayoutKey) -> UiMessage | None:
        for item_key, item in self._documents:
            if item_key == key:
                return item
        return None

    def _build_documents(
        self, projection: ConversationProjection
    ) -> list[tuple[LayoutKey, UiMessage]]:
        return [
            (("message", message.message_id), message)
            for message in projection.messages
        ]

    def _desired_range(self) -> tuple[int, int]:
        if self.size.height <= 0:
            return 0, min(len(self._documents), 20)
        return self._index.visible_range(
            int(self.scroll_y),
            max(1, self.size.height),
            overscan=self._overscan,
        )

    def _sync_visible_range(self, scroll_generation: int | None = None) -> None:
        if (
            scroll_generation is not None
            and scroll_generation != self._auto_scroll_generation
        ):
            return
        if self._projection is None:
            return
        start, end = self._desired_range()
        if (start, end) != self._visible_range:
            self._mount_range(start, end)
            self._update_spacers(start, end)
            anchor = (
                self._restore_anchor
                or self._resize_anchor
                or self._index.locate(int(self.scroll_y))
            )
            self.call_after_refresh(
                self._measure_and_restore,
                anchor,
                False,
                self._auto_scroll_generation,
            )

    def _complete_projection_flush(
        self,
        generation: int,
        auto_scroll_generation: int,
        anchor: tuple[Hashable, int] | None,
        at_bottom: bool,
    ) -> None:
        if generation != self._flush_generation:
            return
        follow_bottom = (
            at_bottom
            and self._follow_bottom
            and auto_scroll_generation == self._auto_scroll_generation
        )
        if not follow_bottom and anchor is None:
            anchor = self._index.locate(int(self.scroll_y))
        self._measure_and_restore(anchor, follow_bottom)
        self._restore_anchor = None

    def _complete_jump_to_latest(self, generation: int) -> None:
        if generation != self._auto_scroll_generation:
            return
        self._scroll_end_internal()
        self.call_after_refresh(self._sync_visible_range, generation)

    def _finish_resize(self) -> None:
        anchor = self._resize_anchor
        if anchor is not None:
            self._measure_and_restore(anchor, False)
            if self._resize_timer is not None:
                self._resize_timer.stop()
            self._resize_timer = self.set_timer(0.05, self._end_resize)

    def _end_resize(self) -> None:
        anchor = self._resize_anchor
        self._resize_timer = None
        if anchor is not None:
            self._measure_and_restore(anchor, False)
        self._resize_anchor = None

    def _mount_range(self, start: int, end: int) -> None:
        wanted = {key for key, _ in self._documents[start:end]}
        for key, widget in tuple(self._widgets.items()):
            if key not in wanted:
                widget.remove()
                del self._widgets[key]
        bottom = self.query_one("#virtual-bottom-spacer", _Spacer)
        for key, item in self._documents[start:end]:
            widget = self._widgets.get(key)
            if widget is None:
                widget = MessageWidget(
                    item,
                    self._md_caches,
                    self._tool_presentations,
                    expanded=self._message_expanded.get(
                        item.message_id, self._detail_default
                    ),
                )
                self._widgets[key] = widget
                self.mount(widget, before=bottom)
        self._visible_range = (start, end)
        self.call_after_refresh(self._reorder_mounted, start, end)

    def _reorder_mounted(self, start: int, end: int) -> None:
        if (start, end) != self._visible_range:
            return
        bottom = self.query_one("#virtual-bottom-spacer", _Spacer)
        mounted = set(self.children)
        for key, _ in self._documents[start:end]:
            widget = self._widgets.get(key)
            if widget is not None and widget in mounted:
                self.move_child(widget, before=bottom)

    def _update_spacers(self, start: int, end: int) -> None:
        ids = self._index.ids
        top_height = (
            self._index.prefix_height(ids[start])
            if start < len(ids)
            else self._index.total_height
        )
        if end:
            last = ids[end - 1]
            bottom_height = (
                self._index.total_height
                - self._index.prefix_height(last)
                - self._height_of(cast(LayoutKey, last))
            )
        else:
            bottom_height = self._index.total_height
        self.query_one("#virtual-top-spacer", _Spacer).styles.height = max(
            0, top_height
        )
        self.query_one("#virtual-bottom-spacer", _Spacer).styles.height = max(
            0, bottom_height
        )

    def _height_of(self, key: LayoutKey) -> int:
        before = self._index.prefix_height(key)
        ids = self._index.ids
        index = ids.index(key)
        after = (
            self._index.total_height
            if index + 1 == len(ids)
            else self._index.prefix_height(ids[index + 1])
        )
        return after - before

    def _measure_and_restore(
        self,
        anchor: tuple[Hashable, int] | None,
        at_bottom: bool,
        scroll_generation: int | None = None,
    ) -> None:
        if (
            scroll_generation is not None
            and scroll_generation != self._auto_scroll_generation
        ):
            return
        changed = False
        for key, widget in self._widgets.items():
            height = max(1, widget.outer_size.height)
            if self._height_of(key) != height:
                self._index.update_height(key, height)
                changed = True
        if changed:
            self._update_spacers(*self._visible_range)
        if at_bottom:
            self._scroll_end_internal()
            self._follow_bottom = True
        elif anchor is not None and anchor[0] in self._index.ids:
            self._scroll_to_internal(self._index.prefix_height(anchor[0]) + anchor[1])
            self._follow_bottom = False
        self._sync_new_content_button()

    def _scroll_to_internal(self, y: int) -> None:
        self._issuing_internal_scroll = True
        try:
            self.scroll_to(y=y, animate=False, immediate=True)
        finally:
            self._issuing_internal_scroll = False

    def _scroll_end_internal(self) -> None:
        self._issuing_internal_scroll = True
        try:
            self.scroll_end(animate=False, immediate=True)
        finally:
            self._issuing_internal_scroll = False

    def _invalidate_scroll_intent(self) -> None:
        self._follow_bottom = False
        self._auto_scroll_generation += 1
        self._restore_anchor = None
        self._resize_anchor = None

    def _prune_state(self, live_keys: set[LayoutKey]) -> None:
        if self._projection is None:
            return
        live_messages = {message.message_id for message in self._projection.messages}
        for key in [key for key in self._md_caches if key[0] not in live_messages]:
            del self._md_caches[key]
        self._message_expanded = {
            key: value
            for key, value in self._message_expanded.items()
            if key in live_messages
        }
        for key, widget in tuple(self._widgets.items()):
            if key not in live_keys:
                widget.remove()
                del self._widgets[key]

    def _at_bottom(self) -> bool:
        return self.scroll_y >= max(0, self._index.total_height - self.size.height - 1)

    def _sync_new_content_button(self) -> None:
        if self.parent is None:
            return
        try:
            button = self.parent.query_one(NewContentButton)
        except NoMatches:
            return
        button.display = bool(self._message_count) and not self._at_bottom()
        if button.display:
            self.call_after_refresh(self._reposition_new_content_button)

    def _reposition_new_content_button(self) -> None:
        parent = self.parent
        if not isinstance(parent, Widget):
            return
        try:
            button = parent.query_one(NewContentButton)
        except NoMatches:
            return
        button.styles.offset = (
            max(0, parent.size.width - button.outer_size.width - 2),
            max(0, parent.size.height - button.outer_size.height - 1),
        )


__all__ = [
    "DEFAULT_OVERSCAN",
    "DetailRequested",
    "MessageViewport",
    "MessageWidget",
    "NewContentButton",
]
