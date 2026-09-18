"""供防护调用方使用的路径解析器外观。"""

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

