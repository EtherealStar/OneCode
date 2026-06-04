"""In-memory session permission grants."""

from __future__ import annotations

from pathlib import Path

from infrastructure.filesystem.paths import contains_path, resolve_path


class SessionPermissionStore:
    """Stores temporary grants for one runtime session only."""

    def __init__(self) -> None:
        self._allowed_directories: set[tuple[str, str, Path]] = set()
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

    def clear(self) -> None:
        self._allowed_directories.clear()
        self._denied_tools.clear()
        self._disabled_tools.clear()
