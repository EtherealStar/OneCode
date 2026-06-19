"""Tool-owned file state cache for read/write side effects."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path

from utils.text_io import read_text_file


MAX_DIFF_CHARS = 4_000


@dataclass
class FileState:
    path: Path
    content: str
    mtime_ns: int
    offset: int | None = None
    limit: int | None = None
    partial: bool = False


@dataclass(frozen=True)
class ChangedTextFile:
    path: Path
    diff: str


class FileStateCache:
    def __init__(self) -> None:
        self._states: dict[Path, FileState] = {}

    def get(self, path: Path) -> FileState | None:
        return self._states.get(path.resolve())

    def set(self, state: FileState) -> None:
        self._states[state.path.resolve()] = state

    def remove(self, path: Path) -> None:
        self._states.pop(path.resolve(), None)

    def snapshot_path(
        self,
        path: Path,
        *,
        offset: int | None = None,
        limit: int | None = None,
        partial: bool = False,
    ) -> FileState | None:
        """Read current disk text and cache mtime for successful file tools."""

        resolved = path.resolve()
        if not resolved.exists() or resolved.is_dir():
            self.remove(resolved)
            return None
        try:
            stat = resolved.stat()
            content = read_text_file(resolved)
        except OSError:
            return None
        state = FileState(
            path=resolved,
            content=content,
            mtime_ns=stat.st_mtime_ns,
            offset=offset,
            limit=limit,
            partial=partial,
        )
        self.set(state)
        return state

    def changed_text_files(self) -> tuple[ChangedTextFile, ...]:
        """Compare cached mtimes with disk and return bounded diffs."""

        changed: list[ChangedTextFile] = []
        for path, cached in list(self._states.items()):
            if cached.partial:
                continue
            if not path.exists():
                self.remove(path)
                continue
            try:
                current_mtime = path.stat().st_mtime_ns
            except OSError:
                continue
            if current_mtime == cached.mtime_ns:
                continue
            try:
                current = read_text_file(path)
            except OSError:
                continue
            self.snapshot_path(path, partial=False)
            diff = _diff_snippet(cached.content, current)
            if diff:
                changed.append(ChangedTextFile(path=path, diff=diff))
        return tuple(changed)


def _diff_snippet(before: str, after: str) -> str:
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    return diff[:MAX_DIFF_CHARS] + "\n[diff truncated]"
