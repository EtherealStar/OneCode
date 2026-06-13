"""In-memory session permission grants."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from infrastructure.filesystem.paths import contains_path, resolve_path


@dataclass(frozen=True)
class SessionPermissionSnapshot:
    allowed_directories: tuple[tuple[str, str, Path], ...]
    allowed_tools: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    denied_skills: tuple[str, ...]
    denied_tools: tuple[str, ...]
    disabled_tools: tuple[str, ...]


class SessionPermissionStore:
    """Stores temporary grants for one runtime session only."""

    def __init__(self) -> None:
        self._allowed_directories: set[tuple[str, str, Path]] = set()
        self._allowed_tools: set[str] = set()
        self._allowed_skills: set[str] = set()
        self._denied_skills: set[str] = set()
        self._denied_tools: set[str] = set()
        self._disabled_tools: set[str] = set()

    def allow_directory(
        self,
        *,
        tool_name: str,
        operation: str,
        directory: Path,
    ) -> None:
        self._allowed_directories.add(
            (tool_name, operation, resolve_path(directory))
        )

    def is_allowed(
        self,
        *,
        tool_name: str,
        operation: str,
        target: Path,
    ) -> bool:
        target_path = resolve_path(target)
        for grant_tool, grant_operation, directory in self._allowed_directories:
            if grant_tool != tool_name or grant_operation != operation:
                continue
            if contains_path(directory, target_path):
                return True
        return False

    def allow_tool(self, tool_name: str) -> None:
        """Allow a whole tool for this session without weakening deny checks."""

        if tool_name:
            self._allowed_tools.add(tool_name)

    def is_tool_allowed(self, tool_name: str) -> bool:
        return tool_name in self._allowed_tools

    def allow_skill(self, skill_name: str) -> None:
        if skill_name:
            self._allowed_skills.add(skill_name.lstrip("/"))

    def deny_skill(self, skill_name: str) -> None:
        if skill_name:
            self._denied_skills.add(skill_name.lstrip("/"))

    def is_skill_allowed(self, skill_name: str) -> bool:
        return skill_name.lstrip("/") in self._allowed_skills

    def is_skill_denied(self, skill_name: str) -> bool:
        return skill_name.lstrip("/") in self._denied_skills

    def deny_tool(self, tool_name: str) -> None:
        if tool_name:
            self._denied_tools.add(tool_name)

    def disable_tool(self, tool_name: str) -> None:
        if tool_name:
            self._disabled_tools.add(tool_name)

    def is_tool_denied(self, tool_name: str) -> bool:
        return tool_name in self._denied_tools

    def is_tool_disabled(self, tool_name: str) -> bool:
        return tool_name in self._disabled_tools

    def snapshot(self) -> SessionPermissionSnapshot:
        """Return a stable copy for read-only UI/reporting code."""

        return SessionPermissionSnapshot(
            allowed_directories=tuple(
                sorted(
                    self._allowed_directories,
                    key=lambda item: (item[0], item[1], str(item[2])),
                )
            ),
            allowed_tools=tuple(sorted(self._allowed_tools)),
            allowed_skills=tuple(sorted(self._allowed_skills)),
            denied_skills=tuple(sorted(self._denied_skills)),
            denied_tools=tuple(sorted(self._denied_tools)),
            disabled_tools=tuple(sorted(self._disabled_tools)),
        )

    def clear(self) -> None:
        self._allowed_directories.clear()
        self._allowed_tools.clear()
        self._allowed_skills.clear()
        self._denied_skills.clear()
        self._denied_tools.clear()
        self._disabled_tools.clear()
