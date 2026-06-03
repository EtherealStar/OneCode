"""Path resolver facade used by guard callers."""

from __future__ import annotations

from pathlib import Path

from infrastructure.filesystem.paths import (
    WriteTargetResolution,
    normalize_path_pattern,
    resolve_path,
    resolve_write_target,
    windows_path,
)

__all__ = [
    "Path",
    "WriteTargetResolution",
    "normalize_path_pattern",
    "resolve_path",
    "resolve_write_target",
    "windows_path",
]

