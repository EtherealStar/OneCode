"""Sandbox boundary types and path classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from infrastructure.filesystem.paths import (
    contains_path,
    normalize_path_pattern,
    resolve_path,
    resolve_write_target,
)


Operation = Literal["read", "write", "list", "delete"]
TargetKind = Literal["file", "directory"]


@dataclass(frozen=True)
class SandboxBoundary:
    cwd: Path
    worktree: Path | None = None
    extra_allowed_dirs: tuple[Path, ...] = ()
    denied_patterns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "cwd", resolve_path(self.cwd))
        if self.worktree is not None and not _is_unsafe_root_worktree(self.worktree):
            object.__setattr__(self, "worktree", resolve_path(self.worktree))
        else:
            object.__setattr__(self, "worktree", None)
        object.__setattr__(
            self,
            "extra_allowed_dirs",
            tuple(resolve_path(path) for path in self.extra_allowed_dirs),
        )


@dataclass(frozen=True)
class InsideWorkspace:
    kind: Literal["inside_workspace"]
    path: Path


@dataclass(frozen=True)
class InsideWorktree:
    kind: Literal["inside_worktree"]
    path: Path


@dataclass(frozen=True)
class InsideExtraAllowed:
    kind: Literal["inside_extra_allowed"]
    path: Path
    root: Path


@dataclass(frozen=True)
class ExternalDirectory:
    kind: Literal["external_directory"]
    path: Path
    parent_dir: Path
    pattern: str


@dataclass(frozen=True)
class Denied:
    kind: Literal["denied"]
    path: Path
    reason: str
    pattern: str | None = None


SandboxDecision = (
    InsideWorkspace
    | InsideWorktree
    | InsideExtraAllowed
    | ExternalDirectory
    | Denied
)


def classify_path(
    boundary: SandboxBoundary,
    input_path: str | Path,
    *,
    operation: Operation = "read",
    kind: TargetKind = "file",
) -> SandboxDecision:
    """Classify a path against the sandbox boundary."""

    # write/delete 目标可能尚不存在，因此通过最近的已存在父目录解析，
    # 而不是要求最终路径已经存在。
    if operation in {"write", "delete"}:
        target = resolve_write_target(input_path, base_dir=boundary.cwd).target
    else:
        target = resolve_path(input_path, base_dir=boundary.cwd)

    # deny pattern 优先于 workspace、worktree 和 extra allowed 判断。
    denied = _match_denied(boundary.denied_patterns, target, base_dir=boundary.cwd)
    if denied is not None:
        return Denied(
            kind="denied",
            path=target,
            reason="Path is blocked by a deny pattern.",
            pattern=denied,
        )

    if contains_path(boundary.cwd, target):
        return InsideWorkspace(kind="inside_workspace", path=target)

    if boundary.worktree is not None and contains_path(boundary.worktree, target):
        return InsideWorktree(kind="inside_worktree", path=target)

    for root in boundary.extra_allowed_dirs:
        if contains_path(root, target):
            return InsideExtraAllowed(
                kind="inside_extra_allowed",
                path=target,
                root=root,
            )

    parent_dir = target if kind == "directory" else target.parent
    pattern = normalize_path_pattern(parent_dir / "*")
    return ExternalDirectory(
        kind="external_directory",
        path=target,
        parent_dir=parent_dir,
        pattern=pattern,
    )


def _is_unsafe_root_worktree(path: Path) -> bool:
    # git worktree 查询失败时可能退化成文件系统根目录；若信任该结果，
    # 会意外允许整个盘符。
    return Path(path) == Path(path).anchor


def _match_denied(
    patterns: tuple[str, ...],
    path: Path,
    *,
    base_dir: Path,
) -> str | None:
    for raw_pattern in patterns:
        pattern_input = _absolutize_pattern(raw_pattern, base_dir=base_dir)
        pattern = normalize_path_pattern(pattern_input)
        if pattern.endswith(("*", "\\*")):
            root = pattern[:-2]
            # 使用路径包含语义而不是字符串前缀，避免混淆 /repo-a 与 /repo
            # 这类兄弟路径。
            if contains_path(root, path):
                return raw_pattern
        else:
            try:
                if resolve_path(pattern) == path:
                    return raw_pattern
            except FileNotFoundError:
                if str(path) == pattern:
                    return raw_pattern
    return None


def _absolutize_pattern(pattern: str, *, base_dir: Path) -> str:
    wildcard = pattern.endswith(("/*", "\\*"))
    target = pattern[:-2] if wildcard else pattern
    path = Path(target)
    if not path.is_absolute():
        path = base_dir / path
    suffix = "/*" if wildcard else ""
    return f"{path}{suffix}"
