"""Tests for the TUI Composer key semantics and cursor math."""

from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from ui.tui.completion import CompletionOverlay
from ui.tui.composer import Composer


class ComposerHost(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.submitted: list[str] = []
        self.cancels = 0
        self.completion_requests: list[tuple[str, int]] = []

    def compose(self) -> ComposeResult:
        yield CompletionOverlay(id="completion-overlay")
        yield Composer(id="composer")

    def on_composer_submitted(self, event: Composer.Submitted) -> None:
        self.submitted.append(event.text)

    def on_composer_cancel_requested(self, event: Composer.CancelRequested) -> None:
        self.cancels += 1

    def on_composer_completion_requested(
        self, event: Composer.CompletionRequested
    ) -> None:
        self.completion_requests.append((event.text, event.cursor_offset))

    @property
    def composer(self) -> Composer:
        return self.query_one(Composer)


def _run(coro):
    return asyncio.run(coro)


def test_enter_submits_non_empty_text() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            app.composer.focus()
            app.composer.text = "hello"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.submitted == ["hello"]

    _run(scenario())


def test_enter_on_empty_does_not_submit() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            app.composer.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.submitted == []

    _run(scenario())


def test_ctrl_j_inserts_newline_instead_of_submitting() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            app.composer.focus()
            app.composer.text = "line"
            app.composer.move_cursor((0, 4))
            await pilot.pause()
            await pilot.press("ctrl+j")
            await pilot.pause()
            assert "\n" in app.composer.text
            assert app.submitted == []

    _run(scenario())


def test_cursor_offset_accounts_for_newlines() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            composer = app.composer
            composer.set_text("first\nsecond")
            composer.move_cursor((1, 3))
            await pilot.pause()
            assert composer.cursor_offset() == len("first\n") + 3

    _run(scenario())


def test_set_text_places_cursor_at_offset() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            composer = app.composer
            composer.set_text("abc\ndef", cursor=5)
            await pilot.pause()
            assert composer.text == "abc\ndef"
            assert composer.cursor_offset() == 5

    _run(scenario())


def test_ctrl_c_posts_cancel_request() -> None:
    async def scenario() -> None:
        app = ComposerHost()
        async with app.run_test() as pilot:
            app.composer.focus()
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.cancels == 1

    _run(scenario())
