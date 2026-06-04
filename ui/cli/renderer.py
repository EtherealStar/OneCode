"""Plain text rendering helpers for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ui.cli.types import CliRuntime

PREVIEW_CHARS = 180


def render_banner(runtime: CliRuntime) -> str:
    return "\n".join(
        [
            "OneCode CLI",
            f"cwd: {runtime.workspace}",
            f"session: {runtime.state.session_id}",
            f"model: {runtime.provider_label} / {runtime.model}",
            "commands: /help /tools /status /history /resume /clear /exit",
        ]
    )


def render_help() -> str:
    return "\n".join(
        [
            "Commands:",
            "  /help              Show commands.",
            "  /tools             List enabled tools.",
            "  /status            Show current runtime status.",
            "  /history [n]       Show recent message summaries.",
            "  /resume <target>   Restore .onecode session id or messages.jsonl path.",
            "  /clear             Start a fresh session without deleting old transcripts.",
            "  /exit, /quit       Flush transcript and exit.",
        ]
    )


def render_running() -> str:
    return "Running..."


def render_assistant(text: str) -> str:
    return text if text else "(assistant returned no text)"


def render_error(message: str) -> str:
    return f"Error: {message}"


def render_tools(descriptors: Iterable[Any]) -> str:
    lines = ["Enabled tools:"]
    for descriptor in descriptors:
        lines.append(f"  {descriptor.name}: {descriptor.description}")
    return "\n".join(lines)


def render_status(runtime: CliRuntime) -> str:
    usage = runtime.state.usage
    transition = (
        runtime.state.last_transition.value
        if runtime.state.last_transition is not None
        else "none"
    )
    transcript_path = runtime.message_store.transcript_store.messages_path
    return "\n".join(
        [
            "Status:",
            f"  workspace: {runtime.workspace}",
            f"  session: {runtime.state.session_id}",
            f"  provider: {runtime.provider_label}",
            f"  model: {runtime.model}",
            f"  turns: {runtime.state.turn_count}/{runtime.state.max_turns}",
            f"  last transition: {transition}",
            (
                "  usage: "
                f"input={usage.input_tokens}, output={usage.output_tokens}, "
                f"cache_read={usage.cache_read_input_tokens}, "
                f"cache_write={usage.cache_creation_input_tokens}"
            ),
            f"  transcript: {_display_path(transcript_path, runtime.workspace)}",
        ]
    )


def render_history(messages: Iterable[dict[str, Any]], *, start_index: int = 1) -> str:
    items = list(messages)
    if not items:
        return "Recent messages: none"

    lines = ["Recent messages:"]
    for index, message in enumerate(items, start=start_index):
        role = _message_role(message)
        detail = _message_detail(message)
        lines.append(f"[{index}] {role}: {detail}")
    return "\n".join(lines)


def render_clear(old_session_id: str, new_session_id: str) -> str:
    return (
        f"Started new session {new_session_id}. "
        f"Previous session {old_session_id} is still in .onecode."
    )


def render_resume(session_id: str, messages_path: Path, workspace: Path) -> str:
    return (
        f"Restored session {session_id} from "
        f"{_display_path(messages_path, workspace)}."
    )


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) else "unknown"


def _message_detail(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "tool_result":
        tool_name = message.get("tool_name") or "unknown_tool"
        call_id = message.get("tool_call_id") or "unknown_call"
        error = " error" if message.get("is_error") is True else ""
        return f"{tool_name} {call_id}{error}: {_preview(message.get('content'))}"

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        names = []
        for call in tool_calls:
            if isinstance(call, dict):
                function = call.get("function")
                if isinstance(function, dict) and isinstance(function.get("name"), str):
                    names.append(function["name"])
        if names:
            return f"<tool call: {', '.join(names)}>"

    return _preview(message.get("content"))


def _preview(value: Any) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        text = " ".join(_preview_block(block) for block in value)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > PREVIEW_CHARS:
        return f"{text[:PREVIEW_CHARS]}..."
    return text


def _preview_block(block: Any) -> str:
    if isinstance(block, dict):
        text = block.get("text")
        if isinstance(text, str):
            return text
    return str(block)


def _display_path(path: Path, workspace: Path) -> str:
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)
