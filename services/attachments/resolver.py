"""Resolve user @mentions within the active workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    for candidate in workspace.rglob("*"):
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(workspace).as_posix().casefold()
        except (OSError, ValueError):
            continue
        if not _inside(resolved, workspace):
            continue
        if candidate.name.casefold() == Path(path_text).name.casefold():
            matches.append(resolved)
            continue
        if relative == normalized:
            matches.append(resolved)
    return sorted(set(matches), key=lambda path: path.as_posix().casefold())


def _inside(path: Path, workspace: Path) -> bool:
    try:
        path.relative_to(workspace)
    except ValueError:
        return False
    return True
