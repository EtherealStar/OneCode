"""Static-region printers for the inline REPL.

The static region is the terminal scrollback. Everything printed here
goes through :func:`print_static`, which uses a Rich console bound to
``sys.stdout`` with *no* background style — letting the host terminal
provide the background. Once printed, lines are never redrawn.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from ui.cli.terminal.markdown_rendering import render_cached_markdown
from ui.cli.tool_renderers import (
    render_fallback_tool_result,
    render_tool_result,
)
from ui.cli.theme import RICH_THEME
from ui.cli.types import CliRuntime


# Module-level console reused by every static printer. Sharing it
# avoids the cost of building a new Rich Console per print, which
# would matter once a long session replays hundreds of tool banners.
_STATIC_CONSOLE: Console | None = None


def static_console() -> Console:
    """Return the process-wide static-region console.

    The theme only defines foreground styles. The actual background
    always comes from the terminal host.
    """

    global _STATIC_CONSOLE
    if _STATIC_CONSOLE is None:
        _STATIC_CONSOLE = Console(theme=RICH_THEME)
    return _STATIC_CONSOLE


def reset_static_console() -> None:
    """Drop the cached console.

    Tests that capture stdout by redirecting ``sys.stdout`` need a
    fresh console pointing at the new stream, so they call this and
    then trigger a rebuild by calling :func:`static_console`.
    """

    global _STATIC_CONSOLE
    _STATIC_CONSOLE = None


def print_static(renderable: Any) -> None:
    """Print a renderable to the static region (terminal scrollback)."""

    static_console().print(renderable)


# --- reverse-video user prompt --------------------------------------------


def user_reverse_style(brightness: str) -> str:
    """Pick a reverse-video style for the user prompt.

    On dark hosts (default) we use ``white on black``; on light hosts
    we use ``black on white``. We avoid Rich's ``reverse`` keyword
    because the resulting colors depend on the active foreground
    style, which is theme-dependent and would render inconsistently
    when the same line is rendered against a light or dark host.
    """

    if brightness == "light":
        return "black on white"
    return "white on black"


def print_user_submitted(line: str, *, brightness: str) -> None:
    """Print a committed user line in reverse video.

    ``line`` is the raw user input. We strip trailing newlines so the
    reverse-video band stays on a single terminal row.
    """

    text = Text(f"> {line.rstrip()}", style=user_reverse_style(brightness))
    print_static(text)


# --- assistant prefix + Markdown commit -----------------------------------


def assistant_prefix_style() -> str:
    """The ``onecode>`` prefix color.

    Uses the same accent as section titles so the prefix reads as
    part of the assistant identity, not a tool bullet.
    """

    return "onecode.title"


def print_assistant_start() -> None:
    """Print the ``onecode>`` prefix in line with the upcoming reply.

    The Markdown body that follows will start on the same row when
    Rich honors ``end=""``. We commit this prefix before streaming
    so a power outage mid-stream still leaves a visible assistant
    marker.
    """

    static_console().print(
        Text("onecode>", style=assistant_prefix_style())
    )


def print_assistant_markdown(text: str) -> None:
    """Commit a complete assistant reply as Markdown.

    Called once when streaming finishes. The function prints the
    ``onecode>`` prefix on a fresh row, then the Markdown body. We
    print the prefix here (rather than relying on a separate
    :func:`print_assistant_start` call) so callers cannot forget the
    prefix and leave the committed assistant text without an
    identity marker.

    The body is rendered through :func:`render_cached_markdown` so
    replays of the same assistant message (e.g. after ``/clear`` or
    session resume) hit the text cache instead of re-lexing.
    """

    if not text:
        return
    static_console().print(
        Text("onecode>", style=assistant_prefix_style())
    )
    width = static_console().width or 80
    cached_lines = render_cached_markdown(text, width=width)
    if cached_lines:
        # Print the rendered lines verbatim; this preserves any
        # colour / table layout we already computed. An empty result
        # (e.g. whitespace-only input) prints nothing.
        body = "\n".join(cached_lines)
        print_static(Text(body))


def print_assistant_inline(text: str) -> None:
    """Print a small inline assistant fragment (e.g. error message).

    Used by command results and error paths that should look like
    assistant output but do not need Markdown rendering.
    """

    static_console().print(
        Text("onecode> ", style=assistant_prefix_style())
        + Text(text, style="onecode.metric")
    )


# --- tool banners ---------------------------------------------------------


def print_tool_banner_start(tool_name: str, call_id: str, arguments: dict[str, Any] | None = None) -> None:
    """Print the opening line of a tool invocation.

    The static region only needs a compact one-line summary, so we
    format the call name and a bounded argument preview directly
    rather than reusing any heavier banner widget.
    """

    label = Text("● ", style="onecode.info") + Text(
        tool_name or "tool", style="onecode.command"
    )
    if call_id:
        label += Text(f" [{call_id}]", style="onecode.subtle")
    print_static(label)
    if arguments:
        preview = _summarize_arguments(arguments)
        if preview:
            print_static(Text(f"  → {preview}", style="onecode.subtle"))


def print_tool_banner_running(call_id: str) -> None:
    """Print a progress marker for a tool still in flight.

    A new spinner line per call would clutter scrollback, so we
    quietly update via a *result line* once the tool finishes. This
    function is kept as a hook for future per-tool progress that
    needs to land in the static region.
    """

    _ = call_id


def print_tool_result(
    result: Any,
    *,
    call_id: str,
    workspace: Path | None = None,
) -> None:
    """Print the result line for a finished tool call.

    The line is wrapped in the unified ``⎿`` container used by every
    tool result in the static region. Specific tool renderers in
    :mod:`ui.cli.tool_renderers` must not embed the container
    themselves; the framework owns it so nesting and styling stay
    consistent across tools.
    """

    if hasattr(result, "tool_call_id"):
        line = render_tool_result(result, workspace=workspace) if workspace is not None else render_fallback_tool_result(result)
    else:
        line = render_fallback_tool_result(result)
    print_static(Text(f"  ⎿  {line}", style="onecode.subtle"))


def print_untrusted_mcp_notice(name: str, detail: str) -> None:
    """Print a one-liner warning for skipped untrusted MCP servers."""

    suffix = f" ({detail})" if detail else ""
    print_static(
        Text(
            f"! Skipped untrusted MCP server: {name}{suffix}. "
            "It was not run; its tools are unavailable.",
            style="onecode.warning",
        )
    )


def _summarize_arguments(arguments: dict[str, Any], *, limit: int = 120) -> str:
    """Format a tool call's input as a one-line preview.

    The tool banners are visual aids; we never want to dump a full
    multi-kilobyte argument dict into the scrollback.
    """

    parts: list[str] = []
    for key, value in arguments.items():
        rendered = _render_argument_value(value)
        parts.append(f"{key}={rendered}")
        if sum(len(part) for part in parts) > limit:
            break
    text = " ".join(parts)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _render_argument_value(value: Any, *, inner_limit: int = 40) -> str:
    if isinstance(value, str):
        compact = " ".join(value.split())
        if len(compact) > inner_limit:
            return f'"{compact[: inner_limit - 1]}…"'
        return f'"{compact}"'
    if isinstance(value, (list, tuple)):
        return f"<{len(value)} items>"
    if isinstance(value, dict):
        return f"<{len(value)} keys>"
    return str(value)


# --- explicit init (so callers can rebuild the console) ------------------


def rebind_static_console() -> Console:
    """Force a rebuild of the static console.

    Tests use this to make the module-level console point at a
    captured stdout.
    """

    reset_static_console()
    return static_console()


def runtime_is_attached(runtime: CliRuntime | None) -> bool:
    """Tiny helper used by other terminal modules to short-circuit
    when the REPL is running without a fully wired runtime (e.g. the
    M0 spike and the test harness).
    """

    return runtime is not None
