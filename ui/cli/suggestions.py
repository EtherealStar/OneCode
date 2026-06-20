"""Prompt suggestions for slash commands, sessions, and file attachments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Iterable, Literal

from services.attachments.ignore import is_ignored_attachment_dir
from ui.cli.commands import visible_commands
from ui.cli.types import CliRuntime

SuggestionKind = Literal["command", "file", "directory", "session"]
MAX_FILE_SUGGESTIONS = 200
MAX_FILE_SUGGESTION_VISITS = 20_000
FILE_SUGGESTION_CACHE_TTL_SECONDS = 5.0
GLOBAL_BASENAME_SEARCH_MIN_CHARS = 2


@dataclass(frozen=True)
class _FileCandidate:
    path: Path
    relative: str
    relative_folded: str
    name_folded: str
    is_dir: bool


@dataclass(frozen=True)
class _FileSuggestionCacheEntry:
    created_at: float
    candidates: tuple[_FileCandidate, ...]


_FILE_SUGGESTION_CACHE: dict[Path, _FileSuggestionCacheEntry] = {}


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
        if prefix.endswith("/"):
            return ()
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
    try:
        resolved_workspace = workspace.resolve()
    except (OSError, ValueError):
        return
    if not normalized:
        entries = _top_level_file_candidates(resolved_workspace)
        candidates = tuple(
            candidate
            for entry in entries
            if (candidate := _candidate_for_path(entry, resolved_workspace)) is not None
        )
    else:
        candidates = _matching_file_candidates(
            resolved_workspace,
            normalized_prefix=normalized.casefold(),
        )
    for candidate in candidates:
        value = f"{candidate.relative}/" if candidate.is_dir else candidate.relative
        yield SuggestionItem(
            id=f"{'directory' if candidate.is_dir else 'file'}:{value}",
            kind="directory" if candidate.is_dir else "file",
            display=value,
            replacement=value,
            description="Directory" if candidate.is_dir else "File",
        )


def _top_level_file_candidates(workspace: Path) -> tuple[Path, ...]:
    try:
        entries = [
            resolved
            for entry in workspace.iterdir()
            if not is_ignored_attachment_dir(entry)
            and (resolved := _resolve_workspace_child(entry, workspace)) is not None
        ]
    except OSError:
        return ()
    return tuple(sorted(entries, key=_file_entry_sort_key)[:MAX_FILE_SUGGESTIONS])


def _matching_file_candidates(
    workspace: Path,
    *,
    normalized_prefix: str,
) -> tuple[_FileCandidate, ...]:
    _base_part, separator, leaf = normalized_prefix.rpartition("/")
    base_prefix = f"{_base_part}/" if separator and _base_part else ""
    allow_basename_search = bool(
        leaf and len(leaf) >= GLOBAL_BASENAME_SEARCH_MIN_CHARS
    )
    matches: list[_FileCandidate] = []
    for candidate in _cached_file_candidates(workspace):
        if _matches_file_prefix(
            candidate,
            normalized_prefix=normalized_prefix,
            base_prefix=base_prefix,
            leaf_prefix=leaf,
            allow_basename_search=allow_basename_search,
        ):
            matches.append(candidate)
            if len(matches) >= MAX_FILE_SUGGESTIONS:
                break
    return tuple(matches)


def _cached_file_candidates(workspace: Path) -> tuple[_FileCandidate, ...]:
    now = time.monotonic()
    cached = _FILE_SUGGESTION_CACHE.get(workspace)
    if (
        cached is not None
        and now - cached.created_at <= FILE_SUGGESTION_CACHE_TTL_SECONDS
    ):
        return cached.candidates
    candidates = _build_file_candidate_index(workspace)
    _FILE_SUGGESTION_CACHE[workspace] = _FileSuggestionCacheEntry(
        created_at=now,
        candidates=candidates,
    )
    return candidates


def _build_file_candidate_index(workspace: Path) -> tuple[_FileCandidate, ...]:
    candidates: list[_FileCandidate] = []
    visited = 0
    stack = [workspace]
    while stack and visited < MAX_FILE_SUGGESTION_VISITS:
        current = stack.pop()
        try:
            children = current.iterdir()
        except OSError:
            continue
        for child in children:
            visited += 1
            if visited > MAX_FILE_SUGGESTION_VISITS:
                break
            if is_ignored_attachment_dir(child):
                continue
            candidate = _candidate_for_path(child, workspace)
            if candidate is None:
                continue
            candidates.append(candidate)
            if _should_descend_file_suggestion_dir(child):
                stack.append(candidate.path)
    return tuple(sorted(candidates, key=_file_candidate_sort_key))


def _file_entry_sort_key(path: Path) -> tuple[bool, str]:
    return (not path.is_dir(), path.as_posix().casefold())


def _file_candidate_sort_key(candidate: _FileCandidate) -> tuple[bool, str]:
    return (not candidate.is_dir, candidate.relative_folded)


def _matches_file_prefix(
    candidate: _FileCandidate,
    *,
    normalized_prefix: str,
    base_prefix: str,
    leaf_prefix: str,
    allow_basename_search: bool,
) -> bool:
    if candidate.relative_folded.startswith(normalized_prefix):
        return True
    if base_prefix and not candidate.relative_folded.startswith(base_prefix):
        return False
    return bool(
        allow_basename_search
        and leaf_prefix
        and candidate.name_folded.startswith(leaf_prefix)
    )


def _resolve_workspace_child(path: Path, workspace: Path) -> Path | None:
    try:
        resolved = path.resolve()
        resolved.relative_to(workspace)
    except (OSError, ValueError):
        return None
    return resolved


def _candidate_for_path(path: Path, workspace: Path) -> _FileCandidate | None:
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return None
    return _FileCandidate(
        path=resolved,
        relative=relative,
        relative_folded=relative.casefold(),
        name_folded=path.name.casefold(),
        is_dir=path.is_dir(),
    )


def _should_descend_file_suggestion_dir(path: Path) -> bool:
    return (
        not is_ignored_attachment_dir(path)
        and not path.is_symlink()
        and path.is_dir()
    )
