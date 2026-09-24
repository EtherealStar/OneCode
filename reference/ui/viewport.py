from __future__ import annotations

from collections.abc import Hashable
from typing import Any
from uuid import UUID

from rich.text import Text
from textual.app import ComposeResult
from textual.css.query import NoMatches
from textual.containers import VerticalScroll
from textual.widgets import Static
from textual.message import Message

from .layout_index import VirtualLayoutIndex
from .projection import UiMessage, UiProjection, UiRunNotice
from .renderers.message import MarkdownCaches, render_message
from .renderers.tool import ToolPresentationRegistry, build_tool_presentation_registry
from pathlib import Path

LayoutKey = tuple[str, Hashable]


class _Spacer(Static):
    can_focus = False


class MessageWidget(Static):
    """一个稳定的、按 message_id 识别的消息控件。"""

    def __init__(
        self,
        message: UiMessage,
        caches: MarkdownCaches,
        tool_presentations: ToolPresentationRegistry,
        *,
        expanded: bool = False,
    ) -> None:
        super().__init__(classes=f"message message-{message.lifecycle.value}")
        self.message_id = message.message_id
        self._caches = caches
        self._tool_presentations = tool_presentations
        self._expanded = expanded
        self._message = message
        self.can_focus = message.lifecycle.value == "queued"
        self.update_message(message, expanded)

    def update_message(self, message: UiMessage, expanded: bool) -> None:
        self._message = message
        self.can_focus = message.lifecycle.value == "queued"
        self._expanded = expanded
        self.set_classes(f"message message-{message.lifecycle.value}")
        self.update(render_message(
            message,
            reasoning_expanded=expanded,
            md_caches=self._caches,
            tool_presentations=self._tool_presentations,
        ))

    def on_click(self) -> None:
        if any(part.kind in {"reasoning", "tool"} for part in self._message.parts) and isinstance(self.parent, MessageViewport):
            self.parent.set_reasoning_expanded(self.message_id, not self._expanded)

    class WithdrawRequested(Message):
        def __init__(self, message_id: UUID) -> None:
            super().__init__()
            self.message_id = message_id

    def key_delete(self) -> None:
        if self._message.lifecycle.value == "queued":
            self.post_message(self.WithdrawRequested(self.message_id))


class RunNoticeWidget(Static):
    def __init__(self, notice: UiRunNotice) -> None:
        super().__init__(classes="run-notice")
        self.notice = notice
        self.update_notice(notice)

    def update_notice(self, notice: UiRunNotice) -> None:
        self.notice = notice
        self.update(Text(notice.display_text, style="ui.error" if notice.is_error else "ui.queued"))


class NewContentButton(Static):
    """离开底部时出现在右下角的返回底部入口。"""

    def on_click(self) -> None:
        if self.parent is None:
            return
        try:
            self.parent.query_one(MessageViewport).jump_to_latest()
        except NoMatches:
            pass


class MessageViewport(VerticalScroll):
    """完整文档高度的虚拟消息视口，只挂载可见区与 overscan。"""

    def __init__(
        self,
        *args,
        tool_presentations: ToolPresentationRegistry | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._tool_presentations = tool_presentations or build_tool_presentation_registry(Path.cwd())
        self._md_caches: MarkdownCaches = {}
        self._projection: UiProjection | None = None
        self._documents: list[tuple[LayoutKey, UiMessage | UiRunNotice]] = []
        self._index = VirtualLayoutIndex(default_height=4)
        self._widgets: dict[LayoutKey, Static] = {}
        self._reasoning_expanded: dict[UUID, bool] = {}
        self._detail_default = False
        self._visible_range = (0, 0)
        self._message_count = 0
        self._resize_anchor = None
        self._resize_timer = None
        self._last_width = 0
        self._restore_anchor = None
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

    def refresh_projection(self, projection: UiProjection) -> None:
        self.sync_projection(projection, {message.message_id for message in projection.messages}, structural=True)

    def sync_projection(self, projection: UiProjection, dirty_message_ids: set[UUID], *, structural: bool = False) -> None:
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
        self._prune_state(projection, set(new_keys))

        start, end = self._desired_range()
        if structural or (start, end) != self._visible_range:
            self._mount_range(start, end)
        for key, item in self._documents[start:end]:
            if key[0] == "message" and item.message_id in dirty_message_ids:
                widget = self._widgets.get(key)
                if isinstance(widget, MessageWidget):
                    widget.update_message(item, self._reasoning_expanded.get(item.message_id, self._detail_default))
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

    def set_reasoning_expanded(self, message_id: UUID, expanded: bool) -> None:
        self._reasoning_expanded[message_id] = expanded
        key = ("message", message_id)
        widget = self._widgets.get(key)
        item = next((item for item_key, item in self._documents if item_key == key), None)
        if isinstance(widget, MessageWidget) and isinstance(item, UiMessage):
            anchor = None if self._at_bottom() else self._index.locate(int(self.scroll_y))
            widget.update_message(item, expanded)
            self.call_after_refresh(
                self._measure_and_restore,
                anchor,
                self._at_bottom(),
                self._auto_scroll_generation,
            )

    def set_all_details_expanded(self, expanded: bool) -> None:
        self._detail_default = expanded
        for message in self._projection.messages if self._projection is not None else ():
            self._reasoning_expanded[message.message_id] = expanded
        at_bottom = self._follow_bottom and self._at_bottom()
        if not at_bottom:
            self._invalidate_scroll_intent()
        anchor = None if at_bottom else self._index.locate(int(self.scroll_y))
        for key, widget in self._widgets.items():
            if not isinstance(widget, MessageWidget):
                continue
            item = next((item for item_key, item in self._documents if item_key == key), None)
            if isinstance(item, UiMessage):
                widget.update_message(item, expanded)
        self.call_after_refresh(
            self._measure_and_restore,
            anchor,
            at_bottom,
            self._auto_scroll_generation,
        )

    def jump_to_latest(self) -> None:
        self._follow_bottom = True
        self._auto_scroll_generation += 1
        generation = self._auto_scroll_generation
        # Textual clamps scroll_y to its current virtual size. Mount the index
        # tail first so spacer geometry exposes the real scroll range.
        tail_y = max(0, self._index.total_height - max(1, self.size.height))
        start, end = self._index.visible_range(tail_y, max(1, self.size.height), overscan=8)
        self._mount_range(start, end)
        self._update_spacers(start, end)
        self.call_after_refresh(self._complete_jump_to_latest, generation)
        self._sync_new_content_button()

    def _complete_jump_to_latest(self, generation: int) -> None:
        if generation != self._auto_scroll_generation:
            return
        self._scroll_end_internal()
        self.call_after_refresh(self._sync_visible_range, generation)

    def scroll_to(
        self,
        x: float | None = None,
        y: float | None = None,
        *,
        animate: bool = True,
        speed: float | None = None,
        duration: float | None = None,
        easing: Any = None,
        force: bool = False,
        on_complete: Any = None,
        level: Any = "basic",
        immediate: bool = False,
    ) -> None:
        if not self._issuing_internal_scroll:
            self._invalidate_scroll_intent()
        super().scroll_to(
            x,
            y,
            animate=animate,
            speed=speed,
            duration=duration,
            easing=easing,
            force=force,
            on_complete=on_complete,
            level=level,
            immediate=immediate,
        )

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        super().watch_scroll_y(old_value, new_value)
        if not self._issuing_internal_scroll and int(old_value) != int(new_value):
            self._invalidate_scroll_intent()
            self._follow_bottom = new_value >= max(0, self._index.total_height - self.size.height - 1)
        if int(old_value) != int(new_value):
            if self._restore_anchor is None and self._resize_anchor is None:
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

    def _build_documents(self, projection: UiProjection) -> list[tuple[LayoutKey, UiMessage | UiRunNotice]]:
        notices_by_message: dict[UUID, list[tuple[int, UiRunNotice]]] = {}
        message_ids = {message.message_id for message in projection.messages}
        unassociated: list[tuple[int, UiRunNotice]] = []
        for index, notice in enumerate(projection.notices):
            target = notices_by_message.setdefault(notice.after_message_id, [])
            if notice.after_message_id in message_ids:
                target.append((index, notice))
            else:
                unassociated.append((index, notice))

        documents: list[tuple[LayoutKey, UiMessage | UiRunNotice]] = []
        for message in projection.messages:
            documents.append((("message", message.message_id), message))
            documents.extend(
                (("notice", (notice.after_message_id, index)), notice)
                for index, notice in notices_by_message.get(message.message_id, ())
            )
        documents.extend(
            (("notice", (notice.after_message_id, index)), notice)
            for index, notice in unassociated
        )
        return documents

    def _desired_range(self) -> tuple[int, int]:
        if self.size.height <= 0:
            return 0, min(len(self._documents), 20)
        return self._index.visible_range(int(self.scroll_y), max(1, self.size.height), overscan=8)

    def _sync_visible_range(self, scroll_generation: int | None = None) -> None:
        if scroll_generation is not None and scroll_generation != self._auto_scroll_generation:
            return
        if self._projection is None:
            return
        start, end = self._desired_range()
        if (start, end) != self._visible_range:
            self._mount_range(start, end)
            self._update_spacers(start, end)
            anchor = self._restore_anchor or self._resize_anchor or self._index.locate(int(self.scroll_y))
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
        anchor,
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
                if isinstance(item, UiMessage):
                    widget = MessageWidget(
                        item,
                        self._md_caches,
                        self._tool_presentations,
                        expanded=self._reasoning_expanded.get(item.message_id, self._detail_default),
                    )
                else:
                    widget = RunNoticeWidget(item)
                self._widgets[key] = widget
                self.mount(widget, before=bottom)
            elif isinstance(widget, RunNoticeWidget) and isinstance(item, UiRunNotice):
                widget.update_notice(item)
        self._visible_range = (start, end)
        # Textual 的 mount 在当前消息循环末尾才把新节点加入 children；随后再用公开 API 校准。
        self.call_after_refresh(self._reorder_mounted, start, end)

    def _reorder_mounted(self, start: int, end: int) -> None:
        if (start, end) != self._visible_range:
            return
        bottom = self.query_one("#virtual-bottom-spacer", _Spacer)
        mounted = set(self.children)
        for key, _ in self._documents[start:end]:
            widget = self._widgets.get(key)
            if widget is not None and widget in mounted:
                # 依次移到 bottom spacer 前，最终顺序与当前 document slice 完全一致。
                self.move_child(widget, before=bottom)

    def _update_spacers(self, start: int, end: int) -> None:
        top_height = self._index.prefix_height(self._index.ids[start]) if start < len(self._index.ids) else self._index.total_height
        if end:
            last = self._index.ids[end - 1]
            bottom_height = self._index.total_height - self._index.prefix_height(last) - self._height_of(last)
        else:
            bottom_height = self._index.total_height
        self.query_one("#virtual-top-spacer", _Spacer).styles.height = max(0, top_height)
        self.query_one("#virtual-bottom-spacer", _Spacer).styles.height = max(0, bottom_height)

    def _height_of(self, key: LayoutKey) -> int:
        before = self._index.prefix_height(key)
        ids = self._index.ids
        index = ids.index(key)
        after = self._index.total_height if index + 1 == len(ids) else self._index.prefix_height(ids[index + 1])
        return after - before

    def _measure_and_restore(
        self,
        anchor,
        at_bottom: bool,
        scroll_generation: int | None = None,
    ) -> None:
        if scroll_generation is not None and scroll_generation != self._auto_scroll_generation:
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

    def _prune_state(self, projection: UiProjection, live_keys: set[LayoutKey]) -> None:
        live_messages = {message.message_id for message in projection.messages}
        for key in [key for key in self._md_caches if key[0] not in live_messages]:
            del self._md_caches[key]
        self._reasoning_expanded = {key: value for key, value in self._reasoning_expanded.items() if key in live_messages}
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
        if self.parent is None:
            return
        try:
            button = self.parent.query_one(NewContentButton)
        except NoMatches:
            return
        button.styles.offset = (
            max(0, self.parent.size.width - button.outer_size.width - 2),
            max(0, self.parent.size.height - button.outer_size.height - 1),
        )
