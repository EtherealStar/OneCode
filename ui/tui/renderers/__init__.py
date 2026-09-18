"""TUI renderers: message bodies, tool presentations, and status bars.

The renderers translate projection value types into Rich renderables. They
never read files, call tools, or depend on Textual widgets, so they are usable
from a plain :class:`rich.console.Console` in tests.
"""

from __future__ import annotations

from ui.tui.renderers.message import MarkdownCaches, render_message
from ui.tui.renderers.status import (
    SPINNER_FRAMES,
    RunState,
    render_run_state,
    render_status_left,
)
from ui.tui.renderers.tool import (
    ToolPresentation,
    ToolPresentationRegistry,
    build_tool_presentation_registry,
    first_line_excerpt,
    redact_sensitive,
)

__all__ = [
    "MarkdownCaches",
    "RunState",
    "SPINNER_FRAMES",
    "ToolPresentation",
    "ToolPresentationRegistry",
    "build_tool_presentation_registry",
    "first_line_excerpt",
    "redact_sensitive",
    "render_message",
    "render_run_state",
    "render_status_left",
]
