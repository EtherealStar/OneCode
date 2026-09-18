from __future__ import annotations

import asyncio
import io

from rich.console import Console
from rich.theme import Theme as RichTheme
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.theme import Theme
from textual.widgets import Static, TextArea

from application.history import HistoryToolCall
from application.types import ToolUpdate
from ui.tui.conversation.viewport import MessageWidget
from ui.tui.projection import ConversationProjection
from ui.tui.renderers.message import render_message
from ui.tui.theme import RICH_STYLES

from tui_test_support import (
    ConversationApp,
    assistant_record,
    make_snapshot,
    text_history,
    user_record,
)


def render_to_text(renderable, width: int = 200) -> str:
    stream = io.StringIO()
    console = Console(
        width=width,
        file=stream,
        force_terminal=False,
        theme=RichTheme(RICH_STYLES),
    )
    console.print(renderable)
    return stream.getvalue()


def _run(coro):
    return asyncio.run(coro)


def _assert_bounded(app: ConversationApp) -> None:
    viewport = app.viewport
    assert viewport.document_count == 5000
    # At most ``viewport height`` messages can be visible; overscan adds <= 16.
    assert viewport.mounted_message_count <= viewport.size.height + 16
    assert viewport.mounted_message_count < 100


def test_textual_core_api_surface_is_available() -> None:
    """M4.1 version gate: run_test, Theme, timer, resize, TextArea, move_child."""

    class _Compat(App[None]):
        def compose(self) -> ComposeResult:
            with Container(id="wrap"):
                yield Static("a", id="a")
                yield Static("b", id="b")
            yield TextArea(id="ta")

        async def on_mount(self) -> None:
            self.register_theme(
                Theme(name="probe", primary="#7AA2F7", background="#101010", dark=True)
            )
            self.theme = "probe"
            self._fired = False
            self._timer = self.set_timer(0.01, self._mark)

        def _mark(self) -> None:
            self._fired = True

    async def scenario() -> None:
        app = _Compat()
        async with app.run_test(size=(60, 20)) as pilot:
            await pilot.pause(0.05)
            assert app._fired is True
            panel = app.query_one("#wrap")
            app.query_one("#a").remove()
            await pilot.pause()
            app.query_one("#wrap").move_child(
                app.query_one("#b"), before=app.query_one("#b")
            )
            await pilot.resize_terminal(40, 12)
            await pilot.pause()
            assert panel.size.width == 40
            app.query_one(TextArea).text = "hello"
            await pilot.pause()
            assert app.query_one(TextArea).text == "hello"

    _run(scenario())


def test_mount_count_is_bounded_at_both_sizes() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=text_history(5000))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            _assert_bounded(app)
            viewport = app.viewport
            viewport.scroll_to(y=viewport._index.total_height // 2, animate=False)
            await pilot.pause(0.2)
            _assert_bounded(app)
            viewport.jump_to_latest()
            await pilot.pause(0.2)
            _assert_bounded(app)

        app2 = ConversationApp(projection)
        async with app2.run_test(size=(60, 20)) as pilot:
            await pilot.pause(0.2)
            _assert_bounded(app2)

    _run(scenario())


def test_unmounted_message_reenters_with_full_content() -> None:
    async def scenario() -> None:
        history = (user_record("u0", "first body"),) + text_history(200)
        projection = ConversationProjection(make_snapshot(history=history))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            viewport = app.viewport
            first = viewport.widget_for("u0")
            assert first is not None
            assert "first body" in render_to_text(render_message(first.message))
            viewport.jump_to_latest()
            await pilot.pause(0.2)
            assert viewport.widget_for("u0") is None
            viewport.scroll_to(y=0, animate=False)
            await pilot.pause(0.2)
            reentered = viewport.widget_for("u0")
            assert reentered is not None
            assert "first body" in render_to_text(render_message(reentered.message))

    _run(scenario())


def test_tool_completion_order_does_not_move_cards() -> None:
    async def scenario() -> None:
        calls = (
            HistoryToolCall("t1", "read_file", {"path": "a.py"}),
            HistoryToolCall("t2", "read_file", {"path": "b.py"}),
        )
        history = (
            assistant_record("a1", "working", assistant_call_id="c1", model_turn_index=0, tool_calls=calls),
        )
        projection = ConversationProjection(make_snapshot(history=history))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            # t2 finishes before t1.
            change = projection.apply(
                ToolUpdate(
                    generation=1,
                    sequence=1,
                    tool_call_id="t2",
                    tool_name="read_file",
                    status="completed",
                    text="b result",
                    assistant_call_id="c1",
                    model_turn_index=0,
                )
            )
            app.push_change(change)
            await pilot.pause(0.05)
            widget = app.viewport.widget_for("a1")
            assert widget is not None
            tool_ids = [p.tool_call_id for p in widget.message.parts if p.tool_name]
            assert tool_ids == ["t1", "t2"]
            by_id = {
                p.tool_call_id: p for p in widget.message.parts if p.tool_name
            }
            assert by_id["t2"].result_preview == "b result"
            assert by_id["t1"].result_preview is None

    _run(scenario())


def test_growth_while_reading_does_not_jump_to_bottom() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(make_snapshot(history=text_history(400)))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            viewport = app.viewport
            viewport.scroll_to(y=viewport._index.total_height // 2, animate=False)
            await pilot.pause(0.2)
            assert viewport._at_bottom() is False
            change = projection.apply(
                ToolUpdate(
                    generation=1,
                    sequence=1,
                    tool_call_id="late",
                    tool_name="read_file",
                    status="completed",
                    text="late result",
                    assistant_call_id="new-call",
                    model_turn_index=999,
                )
            )
            app.push_change(change)
            await pilot.pause(0.2)
            assert viewport._follow_bottom is False
            assert viewport._at_bottom() is False
            viewport.jump_to_latest()
            await pilot.pause(0.2)
            assert viewport._follow_bottom is True
            assert viewport._at_bottom() is True

    _run(scenario())


def test_resize_keeps_reading_anchor() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(make_snapshot(history=text_history(400)))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            viewport = app.viewport
            viewport.scroll_to(y=viewport._index.total_height // 2, animate=False)
            await pilot.pause(0.2)
            await pilot.resize_terminal(60, 20)
            await pilot.pause(0.3)
            assert viewport._at_bottom() is False

    _run(scenario())


def test_deleting_anchored_message_falls_back_to_neighbor() -> None:
    async def scenario() -> None:
        history = text_history(50)
        projection = ConversationProjection(make_snapshot(history=history))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.2)
            viewport = app.viewport
            viewport.scroll_to(y=viewport._index.total_height // 2, animate=False)
            await pilot.pause(0.2)
            anchor = viewport._index.locate(int(viewport.scroll_y))
            assert anchor is not None
            removed_key = anchor[0]
            assert isinstance(removed_key, tuple)
            removed_id = removed_key[1]
            assert isinstance(removed_id, str)
            remaining = tuple(r for r in history if r.uuid != removed_id)
            change = projection.replace(make_snapshot(history=remaining))
            app.push_change(change)
            await pilot.pause(0.3)
            assert removed_id not in viewport._index.ids
            assert viewport.mounted_message_count > 0
            assert viewport._at_bottom() is False

    _run(scenario())
