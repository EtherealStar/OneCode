from __future__ import annotations

from textual.message import Message
from textual.widgets import TextArea
from textual.binding import Binding

from .completion import CompletionOverlay
from .composer_references import (
    ComposerInputSnapshot,
    ComposerReferenceState,
    location_from_offset,
    offset_from_location,
)
from .workspace_completion import WorkspaceQuery
from miniagent.workspace_references import WorkspacePathCandidate


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "submit_text", "提交", show=False),
        Binding("ctrl+enter", "insert_newline", "换行", show=False),
        Binding("tab", "complete", "补全", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        # 聊天输入不需要代码编辑器的行号槽。
        kwargs.setdefault("show_line_numbers", False)
        super().__init__(*args, **kwargs)
        self.reference_state = ComposerReferenceState(self.text)
        self._reference_write = False
    class Submitted(Message):
        def __init__(self, snapshot: ComposerInputSnapshot) -> None:
            super().__init__()
            self.snapshot = snapshot
            self.text = snapshot.text

    class CancelRequested(Message):
        pass

    class CompletionRequested(Message):
        def __init__(self, snapshot: ComposerInputSnapshot, cursor_offset: int) -> None:
            super().__init__()
            self.snapshot = snapshot
            self.text = snapshot.text
            self.cursor_offset = cursor_offset

    def on_key(self, event) -> None:
        overlay = self.app.query_one(CompletionOverlay)
        if overlay.handle_key(event.key):
            event.prevent_default()
            event.stop()
        elif self._handle_reference_key(event.key):
            event.prevent_default()
            event.stop()
        elif event.key == "ctrl+enter":
            self.action_insert_newline()
            event.stop()
        elif event.key == "tab":
            self.action_complete()
            event.stop()
        elif event.key == "enter":
            self.action_submit_text()
            event.stop()
        elif event.key == "ctrl+c":
            event.prevent_default()
            event.stop()
            self.post_message(self.CancelRequested())
        # Escape 不在此处理：它只关闭补全 overlay 或 modal（textual-ui.md §11）。

    def action_submit_text(self) -> None:
        snapshot = self.reference_state.snapshot()
        if snapshot.text.strip() or snapshot.references:
            self.post_message(self.Submitted(snapshot))

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        self.post_message(self.CompletionRequested(self.reference_state.snapshot(), self.cursor_offset()))

    def on_click(self) -> None:
        self.call_later(self._snap_mouse_cursor)

    def _snap_mouse_cursor(self) -> None:
        offset = self.cursor_offset()
        snapped = self.reference_state.snap_cursor(offset)
        if snapped != offset:
            self.move_cursor(location_from_offset(self.text, snapped))

    def sync_external_text(self) -> None:
        if self._reference_write:
            return
        try:
            self.reference_state.reconcile(self.text)
        except ValueError:
            # TextArea 原生操作若切开 token，恢复最近一次有效快照。
            self._write_snapshot(self.reference_state.snapshot())

    def accept_workspace(self, query: WorkspaceQuery, candidate: WorkspacePathCandidate) -> None:
        snapshot = self.reference_state.accept(query.start, query.end, candidate)
        accepted = next(item for item in snapshot.references if item.start == query.start and item.identity == candidate.identity)
        self._write_snapshot(snapshot, cursor_offset=accepted.end)

    def clear_accepted(self) -> None:
        self.reference_state.clear()
        self._write_snapshot(self.reference_state.snapshot())

    def cursor_offset(self) -> int:
        return offset_from_location(self.text, self.cursor_location)

    def _handle_reference_key(self, key: str) -> bool:
        offset = self.cursor_offset()
        selection = getattr(self, "selection", None)
        if selection is not None:
            selection_start = offset_from_location(self.text, selection.start)
            selection_end = offset_from_location(self.text, selection.end)
            start, end = sorted((selection_start, selection_end))
            if start != end:
                expanded = self.reference_state.expand_range(start, end)
                if expanded != (start, end) and (key in {"backspace", "delete"} or len(key) == 1):
                    replacement = key if len(key) == 1 else ""
                    snapshot = self.reference_state.apply_edit(*expanded, replacement)
                    self._write_snapshot(snapshot, cursor_offset=expanded[0] + len(replacement))
                    return True
        if key == "left":
            token = self.reference_state.token_ending_at(offset)
            if token is not None:
                self.move_cursor(location_from_offset(self.text, token.start))
                return True
        if key == "right":
            token = self.reference_state.token_starting_at(offset)
            if token is not None:
                self.move_cursor(location_from_offset(self.text, token.end))
                return True
        if key == "backspace":
            token = self.reference_state.token_ending_at(offset)
            if token is not None:
                snapshot = self.reference_state.apply_edit(token.start, token.end, "")
                self._write_snapshot(snapshot, cursor_offset=token.start)
                return True
        if key == "delete":
            token = self.reference_state.token_starting_at(offset)
            if token is not None:
                snapshot = self.reference_state.apply_edit(token.start, token.end, "")
                self._write_snapshot(snapshot, cursor_offset=token.start)
                return True
        return False

    def _write_snapshot(self, snapshot: ComposerInputSnapshot, *, cursor_offset: int | None = None) -> None:
        self._reference_write = True
        try:
            self.text = snapshot.text
            offset = len(snapshot.text) if cursor_offset is None else min(cursor_offset, len(snapshot.text))
            self.move_cursor(location_from_offset(snapshot.text, offset))
        finally:
            self._reference_write = False
