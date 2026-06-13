"""Inline REPL — the TTY entry point for the OneCode CLI.

The :class:`InlineRepl` ties the static and dynamic regions together
and drives the main loop. It is intentionally small: every visible
behaviour lives in a dedicated module (static output, prompt input,
streaming, transient pages) and this class is just the conductor.

Loop shape::

    while not done:
        submission = prompt.read(queue_mode=agent_running)
        if submission.kind == CANCEL/EXIT:
            shutdown(); break
        echo user line into static region
        if line starts with "/":
            result = dispatch_command(...)
            handle_command_result(result)
        elif agent_running:
            queue.push(line)
        else:
            await run_agent_turn(line)
            if queue: keep going

A "turn" is one full pass through the agent loop, including any
tool calls and queued follow-ups.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Awaitable, Callable

from rich.console import Console
from rich.text import Text

from core.runtime_state import RuntimeState
from ui.cli import renderer
from ui.cli.commands import dispatch_command
from ui.cli.permissions import CliPermissionPrompter
from ui.cli.resume import list_session_summaries, restore_runtime_from_target
from ui.cli.suggestions import SuggestionItem
from ui.cli.terminal.connect_flow import run_connect_flow
from ui.cli.terminal.detect import detect_terminal_brightness
from ui.cli.terminal.page import TransientPage
from ui.cli.terminal.permission_prompt import TtyPermissionPrompter
from ui.cli.terminal.prompt_session import PromptSession, PromptSubmission, SubmissionKind
from ui.cli.terminal.queue import InputQueue
from ui.cli.terminal.selector import SelectorItem, TransientSelector
from ui.cli.terminal.static_output import (
    print_assistant_markdown,
    print_assistant_start,
    print_static,
    print_user_submitted,
)
from ui.cli.terminal.stream_session import StreamingSession
from ui.cli.terminal.trust_prompt import default_trust_prompt
from ui.cli.theme import rich_theme_for
from ui.cli.types import CliRuntime, CommandResult


class InlineRepl:
    """The TTY CLI main loop, implemented with prompt_toolkit + Rich."""

    def __init__(
        self,
        runtime: CliRuntime,
        *,
        permission_prompter: CliPermissionPrompter | None = None,
    ) -> None:
        self._runtime = runtime
        self._brightness = detect_terminal_brightness()
        self._queue = InputQueue()
        self._prompt = PromptSession(runtime, self._queue)
        self._agent_running = False
        self._cancel_requested = False
        self._permission_prompter = permission_prompter or TtyPermissionPrompter()
        # Use the brightness-aware theme so foreground colors stay
        # legible against light or dark hosts. Static region only —
        # the theme never sets a background.
        self._console = Console(theme=rich_theme_for(self._brightness))

    # --- public entry -----------------------------------------------------

    def run(self) -> int:
        """Synchronous entry point. Returns a process exit code."""

        try:
            asyncio.run(self._main_loop())
        except KeyboardInterrupt:
            self._shutdown()
            return 0
        return 0

    # --- main loop --------------------------------------------------------

    async def _main_loop(self) -> None:
        # Print the static banner once. ``renderer.render_banner`` is
        # theme-agnostic so we render it through the brightness-aware
        # console we just created.
        self._console.print(renderer.render_banner(self._runtime))
        self._print_untrusted_mcp_notices(self._runtime)
        self._agent_running = False
        while True:
            submission = await self._prompt.read(queue_mode=self._agent_running)
            if submission.kind is SubmissionKind.EXIT:
                self._shutdown()
                return
            if submission.kind is SubmissionKind.CANCEL:
                # Ctrl-C on an empty prompt: just clear and keep going.
                if self._agent_running:
                    self._cancel_requested = True
                continue
            if submission.kind is SubmissionKind.QUEUE:
                # The user typed into the queue; show a single
                # static-region note so they know it landed.
                print_static(
                    Text(
                        f"… queued: {submission.text}",
                        style="onecode.subtle",
                    )
                )
                continue
            # Plain submit. ``text`` is the literal buffer (or the
            # completion's ``replacement`` when Enter was used to
            # accept a highlighted completion).
            text = submission.text.strip()
            if not text:
                continue
            print_user_submitted(text, brightness=self._brightness)
            if text.startswith("/"):
                await self._handle_command(text)
                if self._runtime is None:
                    return
                continue
            if self._agent_running:
                self._queue.push(text)
                continue
            await self._run_turn(text)
            # Drain queued prompts one by one until empty.
            while self._queue:
                next_text = self._queue.pop()
                if next_text is None:
                    break
                print_user_submitted(next_text, brightness=self._brightness)
                await self._run_turn(next_text)

    # --- command dispatch -------------------------------------------------

    async def _handle_command(self, line: str) -> None:
        result = dispatch_command(self._runtime, line)
        if result.interaction == "resume_selector":
            result = await self._run_resume_selector()
        elif result.interaction == "connect":
            result = await self._run_connect_flow()
        if result.runtime is not None:
            self._runtime = result.runtime
        if result.renderable is not None:
            if result.presentation == "page":
                await self._show_page(result.renderable)
            else:
                self._console.print(result.renderable)
        if result.should_exit:
            self._shutdown()
            self._runtime = None
            return

    async def _run_resume_selector(self) -> CommandResult:
        summaries = list_session_summaries(self._runtime.workspace)
        if not summaries:
            return CommandResult(
                renderable=renderer.render_session_summaries(
                    summaries, self._runtime.workspace
                ),
                presentation="page",
            )

        def detail(summary: object) -> str:
            updated = getattr(summary, "updated_at", None)
            date = updated.strftime("%Y-%m-%d") if updated else ""
            count = getattr(summary, "message_count", 0)
            return f"{date}  {count} messages".strip()

        items = tuple(
            SelectorItem(
                label=getattr(summary, "title", summary.session_id),
                value=summary,
                detail=detail(summary),
            )
            for summary in summaries
        )
        selector: TransientSelector = TransientSelector("Resume", items)
        chosen = await selector.run()
        if chosen is None:
            return CommandResult()
        assert chosen.value is not None
        try:
            resumed = restore_runtime_from_target(self._runtime, chosen.value.session_id)
        except Exception as exc:
            return CommandResult(renderable=renderer.render_error(str(exc)))
        return CommandResult(
            runtime=resumed,
            renderable=renderer.render_group(
                renderer.render_resume(
                    resumed.state.session_id,
                    resumed.message_store.transcript_store.messages_path,
                    resumed.workspace,
                ),
                renderer.render_restored_messages(
                    resumed.message_store.current_messages()
                ),
            ),
            presentation="page",
        )

    async def _run_connect_flow(self) -> CommandResult:
        result = await run_connect_flow(self._runtime)
        if result.cancelled or result.runtime is None:
            return CommandResult(renderable=result.renderable)
        return CommandResult(runtime=result.runtime, renderable=result.renderable)

    async def _show_page(self, renderable: object) -> None:
        """Show a renderable full-screen until the user presses Esc.

        On non-TTY hosts the page is a no-op, so we fall back to
        printing the renderable inline into the static region.
        """

        from ui.cli.terminal.transient import can_enter_alternate_screen

        if not can_enter_alternate_screen():
            self._console.print(renderable)
            return
        page = TransientPage(renderable)
        await page.show()

    # --- agent turn -------------------------------------------------------

    async def _run_turn(self, line: str) -> None:
        """Run one full agent turn with a live preview.

        We hand the agent's event stream to :class:`StreamingSession`,
        which owns the dynamic-region preview and Esc cancellation. The
        session commits the final Markdown to the static region and
        returns the buffer so we can record cancellation state.
        """

        self._agent_running = True
        session = StreamingSession()
        try:
            events = self._agent_events(line)
            await session.run(events)
        except Exception as exc:
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            self._console.print(renderer.render_error(str(exc)))
        finally:
            self._agent_running = False

    async def _agent_events(self, line: str):
        """Yield agent events for ``line``, collecting attachments first.

        Exceptions raised by the loop stream are surfaced as a single
        synthetic ``error`` event so the streaming preview can render
        them in line rather than crashing the REPL.
        """

        attachments = ()
        if self._runtime.attachment_collector is not None:
            attachments = (
                await self._runtime.attachment_collector.collect_for_user_turn(
                    line,
                    self._runtime.state,
                    self._runtime.message_store.current_messages(),
                    is_main_thread=True,
                )
            )
        try:
            async for event in self._runtime.loop.stream(line, attachments=attachments):
                yield event
        except Exception as exc:
            self._runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": self._runtime.state.turn_count},
            )
            self._runtime.error_log_recorder.flush()
            yield _error_event(str(exc))

    # --- shutdown ---------------------------------------------------------

    def _shutdown(self) -> None:
        runtime = self._runtime
        if runtime is None:
            return
        runtime.message_store.flush_transcript()
        runtime.trace_recorder.flush()
        runtime.error_log_recorder.flush()
        if runtime.mcp_manager is not None:
            try:
                asyncio.run(runtime.mcp_manager.close_all())
            except RuntimeError:
                # ``asyncio.run`` raises if a loop is already running
                # in the caller's thread; in that case we let the
                # process exit and rely on the atexit handler to
                # close transports.
                pass

    def _print_untrusted_mcp_notices(self, runtime: CliRuntime) -> None:
        raw = runtime.state.metadata.get("mcp_untrusted_servers", ())
        if not isinstance(raw, (list, tuple)):
            return
        from ui.cli.terminal.static_output import print_untrusted_mcp_notice

        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "unknown")
            command = str(item.get("command") or "")
            args = str(item.get("args") or "")
            detail = command if args == "(none)" else f"{command} {args}".strip()
            print_untrusted_mcp_notice(name, detail)


# --- helpers --------------------------------------------------------------


def _error_event(message: str) -> object:
    from core.stream_events import AgentEvent

    return AgentEvent(type="error", text=message)


__all__ = ["InlineRepl"]