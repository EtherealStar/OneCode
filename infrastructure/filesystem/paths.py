"""跨平台路径规范化辅助函数。

这些辅助函数保持精简且具备确定性，便于进行单元测试。
更高层级的沙箱策略决策归属于 services.guard。
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
    """可能尚不存在的路径的已解析详情。"""

    target: Path
    parent_dir: Path
    existing_parent: Path
    existing_parent_realpath: Path


def _is_windows(platform: str | None = None) -> bool:
    value = platform if platform is not None else os.name
    return value in {"nt", "win32", "windows"}


def windows_path(input_path: str | Path, *, platform: str | None = None) -> str:
    """将常见的类 Unix 格式 Windows 路径规范化为盘符路径。

    Windows 上的示例：
    - /C:/repo -> C:/repo
    - /c/repo -> C:/repo
    - /cygdrive/c/repo -> C:/repo
    - /mnt/c/repo -> C:/repo
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
    """将输入解析为绝对路径。

    已存在的路径使用严格的 realpath 解析。缺失的路径使用稳定的绝对路径，
    且不会掩盖来自现有父目录的非 ENOENT 错误。
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
    """解析写入目标路径，同时保留缺失的末尾路径分段。"""

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
    """将权限或 glob 路径模式规范化为稳定的字符串形式。"""

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
    """当 child 等于 parent 或位于 parent 之下时返回 True。"""

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
    """当任一路径边界包含另一方时返回 True。"""

    return contains_path(a, b) or contains_path(b, a)
