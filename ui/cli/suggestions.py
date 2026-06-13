"""Prompt suggestions for slash commands, sessions, and file attachments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from ui.cli.commands import visible_commands
from ui.cli.types import CliRuntime

SuggestionKind = Literal["command", "file", "directory", "session"]


@dataclass(frozen=True)
class SuggestionItem:
    id: str
    kind: SuggestionKind
    display: str
    replacement: str
    description: str = ""


def suggestions_for(
    runtime: CliRuntime,
    text: str,
    cursor: int,
) -> tuple[SuggestionItem, ...]:
    cursor = min(max(cursor, 0), len(text))
    text_before_cursor = text[:cursor]
    if text_before_cursor.startswith("/") and " " not in text_before_cursor:
        return tuple(_command_suggestions(text_before_cursor))
    if text_before_cursor.startswith("/resume "):
        prefix = text_before_cursor[len("/resume ") :]
        return tuple(_resume_suggestions(runtime, prefix))
    at_index = text_before_cursor.rfind("@")
    if at_index >= 0 and _is_file_completion_context(text_before_cursor, at_index):
        prefix = text_before_cursor[at_index + 1 :]
        return tuple(_file_suggestions(runtime.workspace, prefix))
    return ()


def _command_suggestions(text: str) -> Iterable[SuggestionItem]:
    for spec in visible_commands():
        display = spec.display_name
        if not display.startswith(text):
            continue
        description = spec.description
        if spec.argument_hint:
            description = f"{description} {spec.argument_hint}"
        yield SuggestionItem(
            id=f"command:{spec.name}",
            kind="command",
            display=display,
            replacement=display,
            description=description,
        )


def _resume_suggestions(runtime: CliRuntime, prefix: str) -> Iterable[SuggestionItem]:
    for spec in visible_commands():
        if spec.name != "resume" or spec.parameter_completer is None:
            continue
        for candidate in spec.parameter_completer(runtime, prefix):
            yield SuggestionItem(
                id=f"session:{candidate}",
                kind="session",
                display=candidate,
                replacement=candidate,
                description="Previous session",
            )
        return


def _is_file_completion_context(text: str, at_index: int) -> bool:
    if at_index == 0:
        return True
    return text[at_index - 1].isspace()


def _file_suggestions(workspace: Path, prefix: str) -> Iterable[SuggestionItem]:
    normalized = prefix.replace("\\", "/")
    base_part, separator, leaf = normalized.rpartition("/")
    base = workspace / base_part if separator else workspace
    try:
        resolved_base = base.resolve()
        resolved_workspace = workspace.resolve()
        resolved_base.relative_to(resolved_workspace)
    except (OSError, ValueError):
        return
    try:
        entries = list(base.iterdir())
        entries.sort(key=_file_entry_sort_key)
    except OSError:
        return
    for entry in entries[:200]:
        if leaf and not entry.name.startswith(leaf):
            continue
        value = f"{base_part}/{entry.name}" if base_part else entry.name
        is_dir = entry.is_dir()
        if is_dir:
            value += "/"
        yield SuggestionItem(
            id=f"{'directory' if is_dir else 'file'}:{value}",
            kind="directory" if is_dir else "file",
            display=value,
            replacement=value,
            description="Directory" if is_dir else "File",
        )


def _file_entry_sort_key(path: Path) -> tuple[bool, str]:
    return (not path.is_dir(), path.name.lower())
