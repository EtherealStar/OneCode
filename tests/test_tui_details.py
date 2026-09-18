from __future__ import annotations

import asyncio

from rich.console import Console
from rich.theme import Theme as RichTheme

from application.history import HistoryToolCall
from application.types import DetailLoaded, DetailRef, DetailResult
from ui.tui.projection import ConversationProjection
from ui.tui.renderers.message import render_message
from ui.tui.theme import RICH_STYLES

from tui_test_support import (
    ConversationApp,
    assistant_record,
    make_snapshot,
    tool_result_record,
)

DETAIL_REF = DetailRef(
    session_id="s1",
    kind="tool_result",
    identifier="t1",
    relative_path=".artifacts/t1.txt",
)


def _history(*, externalized: bool = True, missing: bool = False):
    call = HistoryToolCall("t1", "read_file", {"path": "x.py"})
    return (
        assistant_record(
            "a1",
            "working",
            assistant_call_id="c1",
            model_turn_index=0,
            tool_calls=(call,),
        ),
        tool_result_record(
            "tr1",
            tool_call_id="t1",
            tool_name="read_file",
            text="summary text" if not missing else "",
            assistant_call_id="c1",
            model_turn_index=0,
            externalized=externalized,
            external_result_path=".artifacts/t1.txt" if externalized else None,
            missing_external_result=missing,
        ),
    )


def _render(renderable, width: int = 200) -> str:
    import io

    stream = io.StringIO()
    console = Console(
        width=width,
        file=stream,
        force_terminal=False,
        theme=RichTheme(RICH_STYLES),
    )
    console.print(renderable)
    return stream.getvalue()


def test_expand_externalized_tool_requests_detail() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            assert len(app.detail_requests) == 1
            request = app.detail_requests[0]
            assert request.message_id == "a1"
            assert request.detail_ref == DETAIL_REF

    asyncio.run(scenario())


def test_inline_small_result_does_not_request_detail() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=False))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            assert app.detail_requests == []

    asyncio.run(scenario())


def test_loaded_detail_is_shown_and_not_requested_again() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            change = projection.apply(
                DetailLoaded(
                    generation=1,
                    sequence=1,
                    ref=DETAIL_REF,
                    result=DetailResult(success=True, text="FULL TOOL BODY"),
                )
            )
            app.push_change(change)
            await pilot.pause(0.1)
            widget = app.viewport.widget_for("a1")
            assert widget is not None
            rendered = _render(
                render_message(widget.message, details_expanded=True)
            )
            assert "FULL TOOL BODY" in rendered
            app.detail_requests.clear()
            app.viewport.set_message_expanded("a1", False)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            assert app.detail_requests == []

    asyncio.run(scenario())


def test_missing_artifact_keeps_summary_and_stops_retry() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            change = projection.apply(
                DetailLoaded(
                    generation=1,
                    sequence=1,
                    ref=DETAIL_REF,
                    result=DetailResult(success=False, missing=True),
                )
            )
            app.push_change(change)
            await pilot.pause(0.1)
            widget = app.viewport.widget_for("a1")
            assert widget is not None
            part = next(p for p in widget.message.parts if p.tool_name)
            assert part.detail_error == "missing_artifact"
            assert part.detail_ref is not None
            rendered = _render(
                render_message(widget.message, details_expanded=True)
            )
            assert "summary text" in rendered or "x.py" in rendered
            app.detail_requests.clear()
            app.viewport.set_message_expanded("a1", False)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            assert app.detail_requests == []

    asyncio.run(scenario())


def test_unknown_tool_expand_still_shows_body() -> None:
    async def scenario() -> None:
        call = HistoryToolCall("t7", "mcp_thing", {})
        history = (
            assistant_record("a1", "", assistant_call_id="c1", model_turn_index=0, tool_calls=(call,)),
            tool_result_record(
                "tr7",
                tool_call_id="t7",
                tool_name="mcp_thing",
                text="tool output body",
                assistant_call_id="c1",
                model_turn_index=0,
            ),
        )
        projection = ConversationProjection(make_snapshot(history=history))
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            widget = app.viewport.widget_for("a1")
            assert widget is not None
            rendered = _render(
                render_message(widget.message, details_expanded=True)
            )
            assert "tool output body" in rendered

    asyncio.run(scenario())


def test_session_switch_releases_caches_and_details() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.viewport.set_message_expanded("a1", True)
            await pilot.pause(0.1)
            viewport = app.viewport
            assert viewport.widget_for("a1") is not None
            # Replace with a different session.
            replace = projection.replace(
                make_snapshot(session_id="s2", generation=1, history=())
            )
            app.push_change(replace)
            await pilot.pause(0.2)
            assert projection.session_id == "s2"
            assert viewport.widget_for("a1") is None

    asyncio.run(scenario())


def test_late_detail_from_old_session_does_not_touch_new_projection() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.push_change(
                projection.replace(
                    make_snapshot(session_id="s2", generation=1, history=())
                )
            )
            await pilot.pause(0.1)
            change = projection.apply(
                DetailLoaded(
                    generation=1,
                    sequence=1,
                    ref=DETAIL_REF,
                    result=DetailResult(success=True, text="STALE"),
                )
            )
            assert change.empty
            assert app.viewport.mounted_message_count == 0

    asyncio.run(scenario())


def test_unmount_closes_refresh_scheduler() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(
            make_snapshot(history=_history(externalized=True))
        )
        app = ConversationApp(projection)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            scheduler = app.view._scheduler
            assert scheduler.closed is False
        assert scheduler.closed is True

    asyncio.run(scenario())
