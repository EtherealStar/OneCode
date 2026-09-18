"""Shared builders for the TUI view tests.

Importable as ``tui_test_support`` because pytest puts ``tests/`` on
``sys.path`` for these test modules.
"""

from __future__ import annotations

from typing import Any

from application.history import (
    HistoryAttachmentSummary,
    HistoryRecord,
    HistoryToolCall,
)
from application.types import RunState, SessionSnapshot
from textual.app import App, ComposeResult

from ui.tui.conversation import ConversationView, DetailRequested
from ui.tui.projection import ConversationProjection
from ui.tui.projection_types import ViewChange
from ui.tui.theme import apply_theme


def make_snapshot(
    *,
    session_id: str = "s1",
    generation: int = 1,
    sequence: int = 0,
    history: tuple[HistoryRecord, ...] = (),
    run: RunState | None = None,
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        generation=generation,
        sequence=sequence,
        initialized=True,
        configured=True,
        paused=False,
        status="ready",
        run=run or RunState(),
        queue=(),
        history=history,
        interaction=None,
    )


def user_record(
    uuid: str, text: str, *, attachments: tuple[HistoryAttachmentSummary, ...] = ()
) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid,
        parent_uuid=None,
        role="user",
        text=text,
        attachments=attachments,
    )


def assistant_record(
    uuid: str,
    text: str,
    *,
    assistant_call_id: str | None = None,
    model_turn_index: int | None = None,
    tool_calls: tuple[HistoryToolCall, ...] = (),
) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid,
        parent_uuid=None,
        role="assistant",
        text=text,
        tool_calls=tool_calls,
        assistant_call_id=assistant_call_id,
        model_turn_index=model_turn_index,
    )


def tool_result_record(
    uuid: str,
    *,
    tool_call_id: str,
    tool_name: str = "read_file",
    text: str = "",
    is_error: bool = False,
    assistant_call_id: str | None = None,
    model_turn_index: int | None = None,
    externalized: bool = False,
    external_result_path: str | None = None,
    missing_external_result: bool = False,
) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid,
        parent_uuid=None,
        role="tool_result",
        text=text,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        is_error=is_error,
        assistant_call_id=assistant_call_id,
        model_turn_index=model_turn_index,
        externalized=externalized,
        external_result_path=external_result_path,
        missing_external_result=missing_external_result,
    )


def text_history(count: int) -> tuple[HistoryRecord, ...]:
    """``count`` short assistant messages; deterministic fixed content."""

    return tuple(
        assistant_record(
            f"a{index}",
            f"message {index}",
            assistant_call_id=f"call{index}",
            model_turn_index=index,
        )
        for index in range(count)
    )


class ConversationApp(App[None]):
    """Minimal test app: ConversationView plus a detail-request recorder."""

    def __init__(
        self,
        projection: ConversationProjection,
        *,
        scheduler_delay: float = 0.04,
        overscan: int = 8,
    ) -> None:
        super().__init__()
        self.projection = projection
        self.detail_requests: list[DetailRequested] = []
        self._scheduler_delay = scheduler_delay
        self._overscan = overscan

    def compose(self) -> ComposeResult:
        yield ConversationView(
            id="conversation-view",
            scheduler_delay=self._scheduler_delay,
            overscan=self._overscan,
        )

    async def on_mount(self) -> None:
        apply_theme(self)
        self.view.update(
            self.projection,
            ViewChange(structure_changed=True, immediate=True),
        )

    def on_detail_requested(self, event: DetailRequested) -> None:
        self.detail_requests.append(event)

    @property
    def view(self) -> ConversationView:
        return self.query_one(ConversationView)

    @property
    def viewport(self):
        return self.view.viewport

    def push_change(self, change: Any) -> None:
        self.view.update(self.projection, change)
