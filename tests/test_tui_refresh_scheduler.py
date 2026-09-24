from __future__ import annotations

import asyncio

from tui_test_support import ConversationApp, make_snapshot

from application.types import AssistantDelta, MessageCommitted
from ui.tui.conversation.refresh_scheduler import UiRefreshScheduler
from ui.tui.projection import ConversationProjection
from ui.tui.renderers.message import render_message


class FakeTimer:
    def __init__(self, delay: float, callback) -> None:
        self.delay = delay
        self.callback = callback
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True

    def fire(self) -> None:
        self.callback()


def _scheduler():
    scheduled: list[FakeTimer] = []
    flushes: list[tuple[set, bool]] = []

    def schedule(delay, callback):
        timer = FakeTimer(delay, callback)
        scheduled.append(timer)
        return timer

    scheduler = UiRefreshScheduler(
        schedule, lambda dirty, structural: flushes.append((set(dirty), structural))
    )
    return scheduler, scheduled, flushes


def test_coalesces_updates_within_one_window() -> None:
    scheduler, scheduled, flushes = _scheduler()
    scheduler.request({"a"})
    scheduler.request({"b"})
    scheduler.request({"c"}, structural=True)
    assert len(scheduled) == 1
    assert scheduled[0].delay == 0.04
    assert flushes == []
    scheduled[0].fire()
    assert flushes == [({"a", "b", "c"}, True)]
    assert scheduler.requested_updates == 3
    assert scheduler.flush_count == 1


def test_immediate_request_flushes_synchronously() -> None:
    scheduler, scheduled, flushes = _scheduler()
    scheduler.request({"a"})
    scheduler.request({"b"}, immediate=True)
    assert flushes == [({"a", "b"}, False)]
    assert scheduled[0].stopped is True
    scheduler.request({"c"})
    assert len(scheduled) == 2


def test_auxiliary_change_with_empty_dirty_is_not_swallowed() -> None:
    scheduler, scheduled, flushes = _scheduler()
    scheduler.request(set(), structural=True)
    scheduled[0].fire()
    assert flushes == [(set(), True)]


def test_empty_request_is_dropped() -> None:
    scheduler, scheduled, flushes = _scheduler()
    scheduler.request(set())
    scheduled[0].fire()
    assert flushes == []
    assert scheduler.flush_count == 0


def test_close_stops_timer_and_ignores_requests() -> None:
    scheduler, scheduled, flushes = _scheduler()
    scheduler.request({"a"})
    scheduler.close()
    assert scheduled[0].stopped is True
    scheduled[0].fire()
    assert flushes == []
    scheduler.request({"b"})
    assert len(scheduled) == 1


def test_ten_thousand_deltas_keep_full_body() -> None:
    expected = "".join(f"{index % 10}" for index in range(10000))

    async def scenario() -> None:
        projection = ConversationProjection(make_snapshot())
        app = ConversationApp(projection, scheduler_delay=0.04)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            for index in range(10000):
                change = projection.apply(
                    AssistantDelta(
                        generation=1,
                        sequence=index + 1,
                        text=str(index % 10),
                        assistant_call_id="c1",
                        model_turn_index=0,
                    )
                )
                app.push_change(change)
            await pilot.pause(0.2)
            scheduler = app.view._scheduler
            assert scheduler.requested_updates == 10001
            assert scheduler.flush_count == 2
            message = projection.messages[0]
            assert message.parts[0].content == expected
            widget = app.viewport.widget_for(message.message_id)
            assert widget is not None
            assert widget.message.parts[0].content == expected
            rendered = render_to_text(render_message(widget.message))
            assert "0123456789" in rendered
            assert rendered.rstrip().endswith(expected[-20:])

            app.view.refresh_now()

    asyncio.run(scenario())


def test_terminal_commit_is_visible_immediately() -> None:
    async def scenario() -> None:
        projection = ConversationProjection(make_snapshot())
        app = ConversationApp(projection, scheduler_delay=5.0)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            app.push_change(
                projection.apply(
                    AssistantDelta(
                        generation=1,
                        sequence=1,
                        text="partial",
                        assistant_call_id="c1",
                        model_turn_index=0,
                    )
                )
            )
            # Timer is scheduled but far in the future.
            draft = projection.messages[0].message_id
            committed = projection.apply(
                MessageCommitted(
                    generation=1,
                    sequence=2,
                    message_uuid="uuid-final",
                    text="final body",
                    assistant_call_id="c1",
                    model_turn_index=0,
                )
            )
            app.push_change(committed)
            widget = app.viewport.widget_for("uuid-final")
            assert widget is not None
            assert widget.message.parts[0].content == "final body"
            assert projection.messages[0].message_id == "uuid-final"
            del draft

    asyncio.run(scenario())


def render_to_text(renderable, width: int = 200) -> str:
    import io

    from rich.console import Console
    from rich.theme import Theme as RichTheme

    from ui.tui.theme import RICH_STYLES

    stream = io.StringIO()
    console = Console(
        width=width,
        file=stream,
        force_terminal=False,
        theme=RichTheme(RICH_STYLES),
    )
    console.print(renderable)
    return stream.getvalue()
