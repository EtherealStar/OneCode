"""Shared transient interaction host for the inline terminal UI."""

from __future__ import annotations

import asyncio
from typing import Callable

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.terminal.permission_modal import (
    PermissionModal,
    build_permission_choices,
    denied_response,
    render_permission_modal_ansi,
    render_permission_status_fragments,
)


class TerminalInteractionHost:
    """Owns transient modals that must disappear after a user decision."""

    def __init__(self) -> None:
        self.active_permission: PermissionModal | None = None
        self._active_app: Application[None] | None = None

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        loop = asyncio.get_running_loop()
        modal = PermissionModal(
            request=request,
            choices=build_permission_choices(request),
            future=loop.create_future(),
        )
        self.active_permission = modal
        self.invalidate()
        if self._active_app is None or not self._active_app.is_running:
            app = self._build_standalone_app()
            await app.run_async()
        try:
            return await modal.future
        finally:
            if self.active_permission is modal:
                self.active_permission = None
            self.invalidate()

    def bind_app(self, app: Application[None]) -> None:
        self._active_app = app

    def unbind_app(self, app: Application[None]) -> None:
        if self._active_app is app:
            self._active_app = None

    def invalidate(self) -> None:
        app = self._active_app
        if app is not None and app.is_running:
            app.invalidate()

    def handle_key(self, key: str) -> bool:
        modal = self.active_permission
        if modal is None:
            return False
        if key == "up":
            modal.move(-1)
            self.invalidate()
            return True
        if key == "down":
            modal.move(1)
            self.invalidate()
            return True
        if key in {"1", "2", "3"}:
            modal.choose_index(int(key) - 1)
            self._complete(modal.selected.response)
            return True
        if key == "enter":
            self._complete(modal.selected.response)
            return True
        if key == "cancel":
            self._complete(denied_response(interrupted=True))
            return True
        return False

    def render_body(self, *, width: int):
        modal = self.active_permission
        if modal is None:
            return None
        return render_permission_modal_ansi(modal, width=width)

    def render_status(self):
        modal = self.active_permission
        if modal is None:
            return None
        return render_permission_status_fragments(modal)

    def _complete(self, response: PermissionResponse) -> None:
        modal = self.active_permission
        if modal is None:
            return
        if not modal.future.done():
            modal.future.set_result(response)
        self.active_permission = None
        self.invalidate()

    def _build_standalone_app(self) -> Application[None]:
        bindings = self.key_bindings(fallback_cancel=None, exit_on_complete=True)

        def body_text():  # type: ignore[no-untyped-def]
            try:
                width = app.output.get_size().columns  # type: ignore[union-attr]
            except Exception:
                width = 80
            body = self.render_body(width=width)
            return body if body is not None else ""

        def status_text():  # type: ignore[no-untyped-def]
            status = self.render_status()
            if status is not None:
                return status
            return FormattedText([("class:stream-status", "")])

        body = Window(
            content=FormattedTextControl(body_text),
            height=Dimension(min=1),
            wrap_lines=True,
        )
        status = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(status_text),
        )
        app: Application[None] = Application(
            layout=Layout(HSplit([body, status])),
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
        )

        original_run_async = app.run_async

        async def run_async_with_binding(*args, **kwargs):  # type: ignore[no-untyped-def]
            self.bind_app(app)
            try:
                return await original_run_async(*args, **kwargs)
            finally:
                self.unbind_app(app)

        app.run_async = run_async_with_binding  # type: ignore[method-assign]
        return app

    def key_bindings(
        self,
        *,
        fallback_cancel: Callable[[object], None] | None,
        exit_on_complete: bool = False,
    ) -> KeyBindings:
        bindings = KeyBindings()
        modal_active = Condition(lambda: self.active_permission is not None)

        def complete(event, key: str) -> None:  # type: ignore[no-untyped-def]
            if self.handle_key(key) and exit_on_complete:
                event.app.exit()

        @bindings.add(Keys.Up, eager=True, filter=modal_active)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            self.handle_key("up")

        @bindings.add(Keys.Down, eager=True, filter=modal_active)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            self.handle_key("down")

        @bindings.add("1", eager=True, filter=modal_active)
        def _on_one(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "1")

        @bindings.add("2", eager=True, filter=modal_active)
        def _on_two(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "2")

        @bindings.add("3", eager=True, filter=modal_active)
        def _on_three(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "3")

        @bindings.add(Keys.Enter, eager=True, filter=modal_active)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            complete(event, "enter")

        @bindings.add(Keys.Escape, eager=True, filter=modal_active)
        @bindings.add(Keys.ControlC, eager=True, filter=modal_active)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            if self.handle_key("cancel"):
                if exit_on_complete:
                    event.app.exit()
                return
            if fallback_cancel is not None:
                fallback_cancel(event)

        return bindings


__all__ = ["TerminalInteractionHost"]
