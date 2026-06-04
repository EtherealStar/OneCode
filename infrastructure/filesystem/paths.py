"""Cross-platform path normalization helpers.

These helpers are intentionally small and deterministic enough to unit test.
Higher-level sandbox decisions belong in ``services.guard``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_WINDOWS_DRIVE_REWRITES = (
    re.compile(r"^/([a-zA-Z]):(?:[\\/](.*))?$"),
    re.compile(r"^/([a-zA-Z])(?:[\\/](.*))?$"),
    re.compile(r"^/cygdrive/([a-zA-Z])(?:[\\/](.*))?$"),
    re.compile(r"^/mnt/([a-zA-Z])(?:[\\/](.*))?$"),
)


@dataclass(frozen=True)
class WriteTargetResolution:
    """Resolved details for a path that may not exist yet."""

    target: Path
    parent_dir: Path
    existing_parent: Path
    existing_parent_realpath: Path


def _is_windows(platform: str | None = None) -> bool:
    value = platform if platform is not None else os.name
    return value in {"nt", "win32", "windows"}


def windows_path(input_path: str | Path, *, platform: str | None = None) -> str:
    """Normalize common Unix-looking Windows paths to a drive path.

    Examples on Windows:
    - ``/C:/repo`` -> ``C:/repo``
    - ``/c/repo`` -> ``C:/repo``
    - ``/cygdrive/c/repo`` -> ``C:/repo``
    - ``/mnt/c/repo`` -> ``C:/repo``
    """

    path = os.fspath(input_path)
    if not _is_windows(platform):
        return path

    normalized = path.replace("\\", "/")
    for pattern in _WINDOWS_DRIVE_REWRITES:
        match = pattern.match(normalized)
        if not match:
            continue
        drive = match.group(1).upper()
        rest = match.group(2) or ""
        return f"{drive}:/{rest}" if rest else f"{drive}:/"
    return path


def resolve_path(input_path: str | Path, *, base_dir: str | Path | None = None) -> Path:
    """Resolve input to an absolute path.

    Existing paths use strict realpath resolution. Missing paths use a stable
    absolute path without hiding non-ENOENT errors from existing parents.
    """

    candidate = Path(windows_path(input_path))
    if not candidate.is_absolute():
        root = Path(base_dir) if base_dir is not None else Path.cwd()
        candidate = root / candidate

    try:
        return candidate.resolve(strict=True)
    except FileNotFoundError:
        return candidate.resolve(strict=False)


def resolve_write_target(
    input_path: str | Path,
    *,
    base_dir: str | Path | None = None,
) -> WriteTargetResolution:
    """Resolve a write target while preserving missing final path segments."""

    candidate = Path(windows_path(input_path))
    if not candidate.is_absolute():
        root = Path(base_dir) if base_dir is not None else Path.cwd()
        candidate = root / candidate
    candidate = candidate.absolute()

    try:
        target = candidate.resolve(strict=True)
        return WriteTargetResolution(
            target=target,
            parent_dir=target.parent,
            existing_parent=target.parent,
            existing_parent_realpath=target.parent.resolve(strict=True),
        )
    except FileNotFoundError:
        pass

    # 先向上找到真实存在的父目录，再把缺失后缀接回 realpath；
    # 这样既保留符号链接解析，又不要求最终文件或目录已存在。
    missing_parts: list[str] = []
    current = candidate
    while not current.exists():
        missing_parts.append(current.name)
        parent = current.parent
        if parent == current:
            raise FileNotFoundError(f"No existing parent for write target: {input_path}")
        current = parent

    existing_parent_realpath = current.resolve(strict=True)
    rebuilt = existing_parent_realpath
    for part in reversed(missing_parts):
        rebuilt = rebuilt / part

    return WriteTargetResolution(
        target=rebuilt,
        parent_dir=rebuilt.parent,
        existing_parent=current,
        existing_parent_realpath=existing_parent_realpath,
    )


def normalize_path_pattern(pattern: str | Path) -> str:
    """Normalize permission/glob path patterns into stable string form."""

    raw = os.fspath(pattern)
    if raw == "*":
        return raw

    wildcard = raw.endswith(("/*", "\\*"))
    target = raw[:-2] if wildcard else raw
    if target.endswith(":"):
        target = f"{target}{os.sep}"

    resolved = resolve_path(target)
    normalized = str(resolved)
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
    else:
        normalized = normalized.replace("\\", "/")
    return os.path.join(normalized, "*") if wildcard else normalized


def contains_path(parent: str | Path, child: str | Path) -> bool:
    """Return True when child is equal to or nested under parent."""

    parent_path = resolve_path(parent)
    child_path = resolve_path(child)
    try:
        # Path.relative_to 提供边界感知的包含判断；字符串前缀无法安全处理
        # 兄弟目录、盘符根目录或分隔符差异。
        child_path.relative_to(parent_path)
    except ValueError:
        return False
    return True


def overlaps_path(a: str | Path, b: str | Path) -> bool:
    """Return True when either path boundary contains the other."""

    return contains_path(a, b) or contains_path(b, a)
