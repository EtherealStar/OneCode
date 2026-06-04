"""Read-only classification for parsed Bash commands."""

from __future__ import annotations

from dataclasses import dataclass

from tools.bash.ast_model import BashAnalysis, SimpleCommand
from tools.bash.semantics import check_semantics, effective_command_name, strip_safe_wrappers


READONLY_COMMANDS = {
    "pwd",
    "whoami",
    "ls",
    "tree",
    "cat",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
    "diff",
    "grep",
    "rg",
    "sort",
    "uniq",
    "cut",
    "jq",
}
GIT_READONLY_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "remote",
    "rev-parse",
    "ls-files",
}


@dataclass(frozen=True)
class ReadonlyResult:
    read_only: bool
    reason: str | None = None


def classify_readonly(analysis: BashAnalysis) -> ReadonlyResult:
    semantic = check_semantics(analysis)
    if not semantic.ok:
        return ReadonlyResult(False, semantic.reason)
    for command in analysis.commands:
        if _has_write_redirect(command):
            return ReadonlyResult(False, "Command has an output redirect.")
        if not _command_readonly(command):
            name = effective_command_name(command.argv) or "unknown"
            return ReadonlyResult(False, f"Command is not known read-only: {name}")
    return ReadonlyResult(True)


def _command_readonly(command: SimpleCommand) -> bool:
    stripped = strip_safe_wrappers(command.argv)
    if not isinstance(stripped, tuple) or not stripped:
        return False
    name = stripped[0]
    if name == "git":
        return len(stripped) >= 2 and stripped[1] in GIT_READONLY_SUBCOMMANDS
    if name == "find":
        return not any(arg in {"-exec", "-execdir", "-delete", "-ok", "-okdir"} for arg in stripped[1:])
    if name == "sed":
        return not any(arg == "-i" or arg.startswith("-i") for arg in stripped[1:])
    if name == "jq":
        return not any(arg in {"-f", "-L"} or arg.startswith(("--from-file", "--rawfile", "--slurpfile")) for arg in stripped[1:])
    if name in {"python", "python3"}:
        return stripped[1:] in (("--version",), ("-V",))
    if name == "node":
        return stripped[1:] in (("--version",), ("-v",))
    if name == "command":
        return stripped[1:2] in (("-v",), ("-V",))
    return name in READONLY_COMMANDS


def _has_write_redirect(command: SimpleCommand) -> bool:
    return any(redirect.op in {">", ">>", ">|", "&>", "&>>"} for redirect in command.redirects)
