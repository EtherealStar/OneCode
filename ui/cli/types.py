"""Shared CLI runtime types.

The concrete runtime dataclass and assembly now live in
``application.runtime``. This module keeps the historical ``CliRuntime`` name
as an alias so existing CLI code and tests continue to work while the
application layer owns the wiring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from application.runtime import ApplicationRuntime

CliRuntime = ApplicationRuntime

CommandPresentation = Literal["inline", "page"]
CommandInteraction = Literal["resume_selector", "connect"]


@dataclass(frozen=True)
class CommandResult:
    should_exit: bool = False
    runtime: CliRuntime | None = None
    renderable: object | None = None
    presentation: CommandPresentation = "inline"
    interaction: CommandInteraction | None = None
    reset_main_view: bool = False
    # Messages a successful command asks the REPL to replay into the main
    # scrollback using the normal static-output renderers. This is purely a
    # UI replay request (e.g. session resume); it is not a source of truth
    # for the model context, which lives in the runtime's MessageStore.
    replay_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Durable plan-mode attachments to inject into the next model turn. The
    # REPL passes these to ``AgentLoop.stream(prompt, attachments=...)`` so
    # plan-mode transitions become part of the transcript.
    attachments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Optional prompt the command wants the REPL to enqueue after the user
    # finishes the current turn. Used by ``/plan <description>`` so the
    # description becomes the next user message in plan mode.
    queued_prompt: str | None = None


__all__ = [
    "CliRuntime",
    "CommandResult",
    "CommandPresentation",
    "CommandInteraction",
]
