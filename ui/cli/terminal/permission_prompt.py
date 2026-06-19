"""TTY permission prompt for the inline REPL.

Presents a permission request as a transient, erased-when-done panel with
up/down navigation between the policy-provided choices.
"""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.permissions import (
    _confirm_options,
    _prompt_line,
    render_permission_panel,
)


@dataclass(frozen=True)
class _PermissionChoice:
    label: str
    shortcut: str
    response: PermissionResponse


def _response_for_choice(
    option_id: str,
    request: PermissionRequest | None = None,
) -> PermissionResponse:
    """Map a policy option id, legacy fallback value, or shortcut to a response."""

    _ = request
    if option_id in {"allow_once", "y"}:
        return PermissionResponse(action="allow", scope="once")
    if option_id in {"allow_session_directory", "s"}:
        return PermissionResponse(action="allow", scope="session")
    return PermissionResponse(
        action="deny",
        feedback="User denied the permission request.",
    )


def _build_choices(request: PermissionRequest) -> tuple[_PermissionChoice, ...]:
    """Build the navigable choice list from policy-provided options."""

    choices: list[_PermissionChoice] = []
    for index, option in enumerate(request.options, start=1):
        choices.append(
            _PermissionChoice(
                label=option.label,
                shortcut=str(index),
                response=PermissionResponse(
                    action=option.action,
                    scope=option.scope,
                    feedback=(
                        "User denied the permission request."
                        if option.action == "deny"
                        else None
                    ),
                ),
            )
        )
    return tuple(choices)


class TtyPermissionPrompter:
    """Transient TTY permission prompt."""

    def __init__(self) -> None:
        pass

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        from prompt_toolkit.application import run_in_terminal
        from prompt_toolkit.application.current import get_app_or_none

        running = get_app_or_none()
        if running is not None and getattr(running, "is_running", False):
            # A prompt_toolkit Application (the streaming preview) is
            # already running. Launching a second full-screen app would
            # fight over the terminal, so we suspend the active app with
            # ``run_in_terminal`` and ask via a plain confirm instead.
            return await run_in_terminal(lambda: self._blocking_confirm(request))
        return await self._tty_request(request)

    async def _tty_request(
        self,
        request: PermissionRequest,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> PermissionResponse:
        choices = _build_choices(request)
        if not choices:
            return PermissionResponse(action="deny", feedback="No choices configured.")
        index = 0
        selected: PermissionResponse | None = None

        def get_text():  # type: ignore[no-untyped-def]
            lines: list[tuple[str, str]] = []
            for line in render_permission_panel(request).splitlines():
                lines.append(("class:panel", f"{line}\n"))
            lines.append(("", "\n"))
            for current, choice in enumerate(choices):
                marker = "> " if current == index else "  "
                style = "class:selected" if current == index else "class:item"
                lines.append((style, f"{marker}{choice.shortcut}. {choice.label}\n"))
            lines.append(("", "\nEsc to cancel - Up/Down to select - Enter to confirm\n"))
            return FormattedText(lines)

        bindings = KeyBindings()

        def commit(response: PermissionResponse) -> None:
            nonlocal selected
            selected = response

        @bindings.add(Keys.Down, eager=True)
        def _on_down(event) -> None:  # type: ignore[no-untyped-def]
            nonlocal index
            index = (index + 1) % len(choices)
            event.app.invalidate()

        @bindings.add(Keys.Up, eager=True)
        def _on_up(event) -> None:  # type: ignore[no-untyped-def]
            nonlocal index
            index = (index - 1) % len(choices)
            event.app.invalidate()

        @bindings.add(Keys.Enter, eager=True)
        def _on_enter(event) -> None:  # type: ignore[no-untyped-def]
            commit(choices[index].response)
            event.app.exit()

        for choice_index, choice in enumerate(choices):
            @bindings.add(choice.shortcut, eager=True)
            def _on_shortcut(event, choice_index=choice_index) -> None:  # type: ignore[no-untyped-def]
                commit(choices[choice_index].response)
                event.app.exit()

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            commit(
                PermissionResponse(
                    action="deny",
                    feedback="Permission prompt was interrupted.",
                )
            )
            event.app.exit()

        window = Window(content=FormattedTextControl(get_text))
        app: Application[None] = Application(
            layout=Layout(window),
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )
        await app.run_async()
        assert selected is not None
        return selected

    def _blocking_confirm(self, request: PermissionRequest) -> PermissionResponse:
        """Render the panel and read a single confirm line from stdin.

        Used both on non-TTY hosts and — via ``run_in_terminal`` — when
        a streaming preview app is active and a nested full-screen app
        would conflict.
        """

        from ui.cli.input import ConfirmOption, read_confirm_sync

        print(render_permission_panel(request))
        options = _confirm_options(request)
        prompt = _prompt_line(request)
        try:
            choice = read_confirm_sync(prompt, options)
        except (EOFError, KeyboardInterrupt):
            return PermissionResponse(
                action="deny",
                feedback="Permission prompt was interrupted.",
            )
        for option in options:
            if choice == option.value or choice in option.aliases:
                return _response_for_choice(option.value)
        return PermissionResponse(action="deny", feedback="Unrecognized choice.")
