"""Filesystem target extraction for Bash commands."""

from __future__ import annotations

from services.tools.types import ToolTarget
from tools.bash.ast_model import BashAnalysis, Redirect, SimpleCommand
from tools.bash.semantics import strip_safe_wrappers


READ_PATH_COMMANDS = {"cat", "head", "tail", "wc", "stat", "file", "diff", "grep", "sed", "jq"}
LIST_PATH_COMMANDS = {"ls", "tree", "find", "rg"}
WRITE_PATH_COMMANDS = {"touch", "cp", "mv"}
DIR_WRITE_COMMANDS = {"mkdir", "rmdir"}
DELETE_COMMANDS = {"rm"}


def targets_for_analysis(analysis: BashAnalysis) -> tuple[ToolTarget, ...]:
    targets: list[ToolTarget] = []
    for command in analysis.commands:
        targets.extend(_redirect_targets(command.redirects))
        targets.extend(_command_targets(command))
    return tuple(_dedupe_targets(targets))


def _command_targets(command: SimpleCommand) -> list[ToolTarget]:
    stripped = strip_safe_wrappers(command.argv)
    if not isinstance(stripped, tuple) or not stripped:
        return []
    name = stripped[0]
    args = list(stripped[1:])
    if name == "cd":
        return [ToolTarget(kind="directory", operation="list", value=args[0] if args else ".")]
    if name in LIST_PATH_COMMANDS:
        paths = _path_args(args)
        if not paths:
            paths = ["."]
        return [ToolTarget(kind="directory", operation="list", value=path) for path in paths]
    if name in READ_PATH_COMMANDS:
        paths = _path_args(args)
        if name in {"grep", "rg"} and len(paths) <= 1:
            paths = ["."]
        return [ToolTarget(kind="file", operation="read", value=path) for path in paths]
    if name in DIR_WRITE_COMMANDS:
        return [
            ToolTarget(kind="directory", operation="delete" if name == "rmdir" else "write", value=path)
            for path in _path_args(args)
        ]
    if name in DELETE_COMMANDS:
        return [ToolTarget(kind="file", operation="delete", value=path) for path in _path_args(args)]
    if name in WRITE_PATH_COMMANDS:
        operation = "delete" if name == "mv" else "write"
        return [ToolTarget(kind="file", operation=operation, value=path) for path in _path_args(args)]
    return []


def _redirect_targets(redirects: tuple[Redirect, ...]) -> list[ToolTarget]:
    targets: list[ToolTarget] = []
    for redirect in redirects:
        if not redirect.target:
            continue
        if redirect.op in {">", ">>", ">|", "&>", "&>>"}:
            targets.append(ToolTarget(kind="file", operation="write", value=redirect.target))
        elif redirect.op == "<":
            targets.append(ToolTarget(kind="file", operation="read", value=redirect.target))
    return targets


def _path_args(args: list[str]) -> list[str]:
    paths: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg == "--":
            continue
        if arg.startswith("--"):
            if "=" not in arg and arg in {"--glob", "--type", "--context", "--after-context", "--before-context"}:
                skip_next = True
            continue
        if arg.startswith("-") and arg != "-":
            # Most command flags are not paths; the command-specific allowlist
            # intentionally stays small and conservative for the first version.
            continue
        if arg == "-":
            continue
        paths.append(arg)
    return paths


def _dedupe_targets(targets: list[ToolTarget]) -> list[ToolTarget]:
    seen: set[tuple[str, str, str]] = set()
    result: list[ToolTarget] = []
    for target in targets:
        key = (target.kind, target.operation, target.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(target)
    return result
