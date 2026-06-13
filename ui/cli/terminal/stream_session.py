"""Live-streaming preview for the dynamic region (execplan §M4).

While the agent loop runs, the bottom of the screen shows a throttled
preview of the assistant's accumulated text. The preview lives in the
dynamic region (a non-full-screen prompt_toolkit Application with
``erase_when_done=True``) so it disappears when the turn finishes, at
which point the same text is committed to the static scrollback as
Markdown via :func:`commit_final`.

Design notes:

- **Live Markdown via ANSI.** prompt_toolkit can't host a Rich
  renderable directly, so we render the in-flight Markdown to ANSI
  text with a Rich console writing to a string, then wrap it in
  :class:`prompt_toolkit.formatted_text.ANSI`. This gives genuine
  live Markdown formatting in the dynamic region (the ``live_md``
  decision in the execplan), not just plain text.

- **Bounded height.** A long reply would overflow the dynamic region
  and break ``erase_when_done`` cleanup, so the preview only shows the
  last :data:`_PREVIEW_MAX_LINES` rendered lines. The full text is
  always committed to the static region at the end.

- **Partial code fences.** When the Markdown buffer has an odd number
  of ``\`\`\``` fences we render it as plain text instead, because
  rendering an unbalanced fence leaks a stray ``\`\`\`` into the
  preview.

- **Throttling.** Re-rendering Markdown on every delta would dominate
  the CPU budget, so redraws are throttled to ~20fps
  (:data:`_THROTTLE_INTERVAL`).

- **Cancellation.** ``Esc`` sets a cancel flag and exits the preview
  app; the REPL then stops draining the agent stream and prints a
  cancellation notice.
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass, field

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from ui.cli.terminal.static_output import (
    print_assistant_markdown,
    print_static,
    print_tool_banner_running,
    print_tool_banner_start,
    print_tool_result,
)
from ui.cli.theme import RICH_THEME


_THROTTLE_INTERVAL = 0.05  # 50ms = 20fps
_PREVIEW_MAX_LINES = 12


@dataclass
class StreamBuffer:
    """Mutable accumulator for the in-flight assistant turn."""

    text: str = ""
    cancel_requested: bool = False
    finalised: bool = False
    tool_banner_count: int = 0
    active_tool_ids: set[str] = field(default_factory=set)
    current_tool_label: str = ""


# --- event consumption -----------------------------------------------------


def consume_event(buffer: StreamBuffer, event: object) -> None:
    """Translate one :class:`core.stream_events.AgentEvent` to output.

    Assistant deltas accumulate into ``buffer.text``; tool lifecycle
    events are written to the **static** region immediately so they
    are visible while the turn is still running.
    """

    event_type = getattr(event, "type", None)
    metadata = getattr(event, "metadata", None) or {}
    if event_type == "assistant_delta":
        buffer.text += getattr(event, "text", "") or ""
    elif event_type == "tool_call_ready":
        tool_call = metadata.get("tool_call")
        tool_name = getattr(tool_call, "name", "") if tool_call else ""
        call_id = getattr(tool_call, "id", None) if tool_call else None
        arguments = getattr(tool_call, "input", None) if tool_call else None
        if call_id:
            buffer.active_tool_ids.add(call_id)
            buffer.tool_banner_count += 1
            buffer.current_tool_label = tool_name or "tool"
            print_tool_banner_start(
                tool_name or "tool",
                call_id,
                arguments if isinstance(arguments, dict) else None,
            )
    elif event_type == "tool_started":
        call_id = metadata.get("tool_call_id")
        tool_name = metadata.get("tool_name") or ""
        if tool_name:
            buffer.current_tool_label = tool_name
        if call_id:
            print_tool_banner_running(call_id)
    elif event_type == "tool_progress":
        call_id = metadata.get("tool_call_id")
        if call_id:
            print_tool_banner_running(call_id)
    elif event_type == "tool_result":
        result = getattr(event, "result", None)
        call_id = metadata.get("tool_call_id") or getattr(result, "tool_call_id", None)
        if call_id and call_id in buffer.active_tool_ids:
            buffer.active_tool_ids.discard(call_id)
        buffer.current_tool_label = ""
        print_tool_result(result, call_id=str(call_id or ""))
    elif event_type == "completed":
        # Final assistant text already accumulated from deltas. Some
        # providers only emit the full text on ``completed`` (no
        # deltas) — capture it as a fallback.
        text = getattr(event, "text", "") or ""
        if text and not buffer.text:
            buffer.text = text
    elif event_type == "error":
        message = getattr(event, "text", "") or "error"
        print_static(Text(f"! {message}", style="onecode.error"))


# --- live preview rendering -------------------------------------------------


def render_preview_ansi(buffer: StreamBuffer, *, width: int) -> ANSI:
    """Render the in-flight text to bounded ANSI for the dynamic region."""

    text = buffer.text
    if not text and not buffer.current_tool_label:
        return ANSI("")
    out = io.StringIO()
    console = Console(
        file=out,
        force_terminal=True,
        color_system="standard",
        width=max(width, 20),
        theme=RICH_THEME,
    )
    if text:
        if text.count("```") % 2 == 1:
            # Unbalanced fence: render as plain text to avoid a stray
            # closing fence leaking into the preview.
            console.print(Text(text, style="onecode.metric"))
        else:
            console.print(Markdown(text))
    rendered = out.getvalue()
    # Keep only the last N lines so the dynamic region stays bounded.
    lines = rendered.splitlines()
    if len(lines) > _PREVIEW_MAX_LINES:
        lines = ["  …"] + lines[-_PREVIEW_MAX_LINES:]
    return ANSI("\n".join(lines))


# --- the streaming session --------------------------------------------------


class StreamingSession:
    """Run the live preview while draining an agent event stream."""

    def __init__(self, *, throttle: float = _THROTTLE_INTERVAL) -> None:
        self.buffer = StreamBuffer()
        self._throttle = throttle
        self._cancel = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    async def run(
        self,
        events,  # async iterator of AgentEvent
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> StreamBuffer:
        """Drive the preview app and the event feeder concurrently.

        Returns the final :class:`StreamBuffer`. The accumulated text
        is committed to the static region here so callers don't have
        to remember to call :func:`commit_final`.
        """

        app = self._build_app(input=input, output=output)
        feeder = asyncio.create_task(self._feed(events, app))
        try:
            await app.run_async()
        finally:
            # If Esc exited the app while the feeder was still awaiting
            # the model, cancel the feeder so a long call is dropped.
            if not feeder.done():
                self._cancel.set()
                feeder.cancel()
                try:
                    await feeder
                except asyncio.CancelledError:
                    pass
        self.buffer.finalised = True
        if self._cancel.is_set():
            print_static(Text("已取消", style="onecode.warning"))
        commit_final(self.buffer)
        return self.buffer

    async def _feed(self, events, app: Application) -> None:
        last_render = 0.0
        try:
            async for event in events:
                if self._cancel.is_set():
                    break
                consume_event(self.buffer, event)
                now = time.monotonic()
                if (now - last_render) >= self._throttle:
                    last_render = now
                    _safe_invalidate(app)
        finally:
            _safe_invalidate(app)
            _safe_exit(app)

    def _build_app(
        self,
        *,
        input=None,  # type: ignore[no-untyped-def]
        output=None,  # type: ignore[no-untyped-def]
    ) -> Application[None]:
        bindings = KeyBindings()

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add(Keys.ControlC, eager=True)
        def _on_cancel(event) -> None:  # type: ignore[no-untyped-def]
            self.buffer.cancel_requested = True
            self._cancel.set()
            event.app.exit()

        def preview_text():  # type: ignore[no-untyped-def]
            try:
                width = app.output.get_size().columns  # type: ignore[union-attr]
            except Exception:
                width = 80
            return render_preview_ansi(self.buffer, width=width)

        def status_text():  # type: ignore[no-untyped-def]
            if self.buffer.current_tool_label:
                label = f"tool: {self.buffer.current_tool_label}"
            else:
                label = "thinking…"
            return FormattedText(
                [
                    ("class:stream-prefix", "onecode> "),
                    ("class:stream-status", f"{label}  (Esc to cancel)"),
                ]
            )

        preview_window = Window(
            content=FormattedTextControl(preview_text),
            height=Dimension(min=1, max=_PREVIEW_MAX_LINES + 1),
            wrap_lines=True,
        )
        status_window = Window(
            height=Dimension(min=1, max=1),
            content=FormattedTextControl(status_text),
        )
        layout = Layout(HSplit([preview_window, status_window]))
        app: Application[None] = Application(
            layout=layout,
            full_screen=False,
            erase_when_done=True,
            mouse_support=False,
            key_bindings=bindings,
            input=input,
            output=output,
        )
        return app


def commit_final(buffer: StreamBuffer) -> None:
    """Commit the accumulated text to the static region as Markdown.

    Called once when the turn ends (after the live preview erased).
    """

    if not buffer.text.strip():
        return
    print_assistant_markdown(buffer.text)


# --- defensive helpers ------------------------------------------------------


def _safe_invalidate(app: Application) -> None:
    try:
        if app.is_running:
            app.invalidate()
    except Exception:
        pass


def _safe_exit(app: Application) -> None:
    try:
        if app.is_running:
            app.exit()
    except Exception:
        pass


__all__ = [
    "StreamBuffer",
    "StreamingSession",
    "commit_final",
    "consume_event",
    "render_preview_ansi",
]
