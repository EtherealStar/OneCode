"""Transient permission modal state and rendering."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console
from rich.text import Text

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.permissions import render_permission_request_summary
from ui.cli.theme import RICH_THEME


@dataclass(frozen=True)
class PermissionChoice:
    label: str
    shortcut: str
    response: PermissionResponse


@dataclass
class PermissionModal:
    request: PermissionRequest
    choices: tuple[PermissionChoice, ...]
    future: asyncio.Future[PermissionResponse]
    selected_index: int = 0

    @property
    def selected(self) -> PermissionChoice:
        return self.choices[self.selected_index]

    def move(self, delta: int) -> None:
        self.selected_index = (self.selected_index + delta) % len(self.choices)

    def choose_index(self, index: int) -> None:
        if 0 <= index < len(self.choices):
            self.selected_index = index


def build_permission_choices(request: PermissionRequest) -> tuple[PermissionChoice, ...]:
    """Build the three transient choices from policy-provided options."""

    if len(request.options) != 3:
        raise ValueError("TTY permission prompts require exactly three options.")
    choices: list[PermissionChoice] = []
    for index, option in enumerate(request.options, start=1):
        feedback = (
            "User denied the permission request." if option.action == "deny" else None
        )
        choices.append(
            PermissionChoice(
                label=_choice_label(option.label, option.scope, option.action),
                shortcut=str(index),
                response=PermissionResponse(
                    action=option.action,
                    scope=option.scope,
                    feedback=feedback,
                ),
            )
        )
    return tuple(choices)


def denied_response(*, interrupted: bool = False) -> PermissionResponse:
    return PermissionResponse(
        action="deny",
        feedback=(
            "Permission prompt was interrupted."
            if interrupted
            else "User denied the permission request."
        ),
    )


def render_permission_modal_ansi(modal: PermissionModal, *, width: int) -> ANSI:
    out = io.StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=RICH_THEME,
    )
    console.print(Text(render_permission_request_summary(modal.request), style="onecode.metric"))
    console.print()
    console.print(Text("Do you want to proceed?", style="onecode.permission"))
    for index, choice in enumerate(modal.choices):
        marker = "> " if index == modal.selected_index else "  "
        style = "onecode.permission" if index == modal.selected_index else "onecode.metric"
        console.print(Text(f"{marker}{choice.shortcut}. {choice.label}", style=style))
    console.print()
    console.print(Text("Esc to cancel - Up/Down to select - Enter to confirm", style="onecode.subtle"))
    return ANSI(out.getvalue())


def render_permission_status_fragments(modal: PermissionModal) -> FormattedText:
    return FormattedText(
        [
            ("class:stream-prefix", "onecode> "),
            (
                "class:stream-status",
                f"permission: {modal.request.descriptor.name}  (1/2/3, Enter, Esc)",
            ),
        ]
    )


def _choice_label(label: str, scope: str, action: str) -> str:
    if action == "deny":
        return "No"
    if scope == "once":
        return "Yes"
    if scope == "session":
        if label:
            return f"Yes, {label}"
        return "Yes, allow during this session"
    return label


__all__ = [
    "PermissionChoice",
    "PermissionModal",
    "build_permission_choices",
    "denied_response",
    "render_permission_modal_ansi",
    "render_permission_status_fragments",
]
