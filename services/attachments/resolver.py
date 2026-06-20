"""Resolve user @mentions within the active workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from services.attachments.ignore import (
    ATTACHMENT_IGNORED_DIRS,
    is_ignored_attachment_dir,
)
from services.attachments.parser import AtMention


ResolutionErrorKind = Literal["not_found", "ambiguous", "outside_workspace"]


@dataclass(frozen=True)
class ResolvedMention:
    mention: AtMention
    path: Path
    is_directory: bool


@dataclass(frozen=True)
class ResolutionError:
    mention: AtMention
    error: ResolutionErrorKind
    message: str
    candidates: tuple[str, ...] = ()


def resolve_mention(
    mention: AtMention,
    workspace: Path,
) -> ResolvedMention | ResolutionError:
    """Resolve a mention without ever selecting a path outside workspace."""

    workspace = workspace.resolve()
    exact = (workspace / mention.path_text).resolve()
    if _contains_ignored_dir(exact, workspace):
        return ResolutionError(mention, "not_found", "Mentioned path was not found.")
    if _inside(exact, workspace) and exact.exists():
        return ResolvedMention(mention, exact, exact.is_dir())
    if not _inside(exact, workspace):
        return ResolutionError(
            mention,
            "outside_workspace",
            "Mention resolves outside the workspace.",
        )

    matches = _search_matches(mention.path_text, workspace)
    if not matches:
        return ResolutionError(mention, "not_found", "Mentioned path was not found.")
    if len(matches) > 1:
        return ResolutionError(
            mention,
            "ambiguous",
            "Mentioned path matched multiple workspace entries.",
            candidates=tuple(str(path.relative_to(workspace)) for path in matches[:10]),
        )
    match = matches[0]
    return ResolvedMention(mention, match, match.is_dir())


def _search_matches(path_text: str, workspace: Path) -> list[Path]:
    normalized = path_text.replace("\\", "/").casefold()
    matches: list[Path] = []
    stack = [workspace]
    while stack:
        current = stack.pop()
        try:
            children = current.iterdir()
        except OSError:
            continue
        for candidate in children:
            if is_ignored_attachment_dir(candidate):
                continue
            try:
                resolved = candidate.resolve()
                relative = resolved.relative_to(workspace).as_posix().casefold()
            except (OSError, ValueError):
                continue
            if not _inside(resolved, workspace):
                continue
            if candidate.name.casefold() == Path(path_text).name.casefold():
                matches.append(resolved)
            elif relative == normalized:
                matches.append(resolved)
            if candidate.is_dir() and not candidate.is_symlink():
                stack.append(resolved)
    return sorted(set(matches), key=lambda path: path.as_posix().casefold())


def _inside(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True


def _contains_ignored_dir(path: Path, workspace: Path) -> bool:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        return False
    return any(part.casefold() in ATTACHMENT_IGNORED_DIRS for part in relative.parts)
