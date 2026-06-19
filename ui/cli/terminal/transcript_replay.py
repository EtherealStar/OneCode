"""Replay a restored message chain into the main static region.

When a session is resumed, its historical messages must appear in the
terminal scrollback exactly as they would have during the original
session. This module is the single entry point that walks a restored
message chain and re-emits it through the *normal* static-output
renderers in :mod:`ui.cli.terminal.static_output`.

Design constraints (see
``docs/exec-plans/active/cli-resume-true-repl-recovery.md``):

- No resume-specific summary format. User lines reuse the reverse-video
  :func:`print_user_submitted`, assistant replies reuse
  :func:`print_assistant_markdown`, and tool results reuse
  :func:`print_tool_result`. As the normal rendering paths evolve, resume
  automatically follows.
- This function only replays into the static region. It does not mutate the
  ``MessageStore``, execute tools, call a provider, or write traces. It
  consumes already-restored message dicts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from services.tools.types import ToolExecutionResult
from ui.cli.terminal.static_output import (
    print_assistant_markdown,
    print_tool_result,
    print_user_submitted,
)


def replay_messages_to_static(
    messages: Iterable[dict[str, Any]],
    *,
    brightness: str,
    workspace: Path | None = None,
) -> None:
    """Replay restored messages into the static region (scrollback).

    Messages are emitted in order. Each role is routed to the same
    static-output function the live session uses, so restored history is
    visually identical to a normal session.
    """

    for message in messages:
        role = message.get("role")
        if role == "user":
            _replay_user(message, brightness=brightness)
        elif role == "assistant":
            _replay_assistant(message)
        elif role == "tool_result":
            _replay_tool_result(message, workspace=workspace)
        # ``attachment`` and any unknown roles are intentionally skipped:
        # the live main screen has no stable static rendering for them, so
        # resume must not invent one.


def _replay_user(message: dict[str, Any], *, brightness: str) -> None:
    text = _message_text(message.get("content"))
    if text:
        print_user_submitted(text, brightness=brightness)


def _replay_assistant(message: dict[str, Any]) -> None:
    text = _message_text(message.get("content"))
    # Assistant messages that only carry tool calls (no displayable text)
    # produce nothing here, matching the live scrollback which does not
    # print a synthetic "assistant: <tool call>" line.
    if text:
        print_assistant_markdown(text)


def _replay_tool_result(message: dict[str, Any], *, workspace: Path | None) -> None:
    metadata = message.get("metadata")
    result = ToolExecutionResult(
        tool_call_id=str(message.get("tool_call_id") or ""),
        tool_name=str(message.get("tool_name") or "unknown_tool"),
        content=_message_text(message.get("content")),
        is_error=message.get("is_error") is True,
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    print_tool_result(result, call_id=result.tool_call_id, workspace=workspace)


def _message_text(content: Any) -> str:
    """Extract displayable text from a message ``content`` field.

    Content is normally a plain string, but may be a list of content
    blocks (e.g. multimodal). We concatenate the text of each block and
    ignore non-text parts.
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


__all__ = ["replay_messages_to_static"]
