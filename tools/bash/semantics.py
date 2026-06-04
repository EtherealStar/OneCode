"""Bash semantic checks and exit-code interpretation."""

from __future__ import annotations

from dataclasses import dataclass
import re

from tools.bash.ast_model import BashAnalysis


SHELL_KEYWORDS = {
    "if",
    "then",
    "else",
    "elif",
    "fi",
    "for",
    "while",
    "until",
    "do",
    "done",
    "case",
    "esac",
    "function",
    "{",
    "}",
    "!",
    "[[",
    "]]",
}
EVAL_LIKE = {
    "eval",
    "source",
    ".",
    "exec",
    "trap",
    "enable",
    "alias",
}
CODE_EXEC_FLAGS = {
    "bash",
    "sh",
    "zsh",
    "python",
    "python3",
    "node",
}


@dataclass(frozen=True)
class SemanticResult:
    ok: bool
    reason: str | None = None


@dataclass(frozen=True)
class WrapperError:
    reason: str


@dataclass(frozen=True)
class ExitInterpretation:
    is_error: bool
    message: str | None = None


def check_semantics(analysis: BashAnalysis) -> SemanticResult:
    """Reject argv shapes where the executable cannot be trusted."""

    for command in analysis.commands:
        stripped = strip_safe_wrappers(command.argv)
        if isinstance(stripped, WrapperError):
            return SemanticResult(False, stripped.reason)
        if not stripped:
            return SemanticResult(False, "Empty command.")
        name = stripped[0]
        if name == "" or name.startswith(("-", "|", "&")):
            return SemanticResult(False, "Command appears to be an incomplete fragment.")
        if name in SHELL_KEYWORDS:
            return SemanticResult(False, f"Shell keyword '{name}' is not executable.")
        if name in EVAL_LIKE:
            if name == "alias" and len(stripped) == 1:
                continue
            return SemanticResult(False, f"'{name}' evaluates arguments as shell code.")
        if name == "command" and stripped[1:2] not in (("-v",), ("-V",)):
            return SemanticResult(False, "'command' may bypass shell lookup rules.")
        if name == "fc" and any(re.match(r"^-[^-]*[es]", arg) for arg in stripped[1:]):
            return SemanticResult(False, "'fc' can re-execute shell history.")
        if name in CODE_EXEC_FLAGS and "-c" in stripped[1:]:
            return SemanticResult(False, f"'{name} -c' executes dynamic code.")
        if name == "env" and any(arg == "-S" for arg in stripped[1:]):
            return SemanticResult(False, "env -S splits and re-parses command text.")
        if name == "jq":
            if any("system(" in arg for arg in stripped[1:]):
                return SemanticResult(False, "jq system() executes shell commands.")
            if any(
                arg in {"-f", "-L", "--from-file", "--rawfile", "--slurpfile"}
                or arg.startswith(("--from-file=", "--rawfile=", "--slurpfile=", "--library-path="))
                for arg in stripped[1:]
            ):
                return SemanticResult(False, "jq file-loading flags are not auto-allowed.")
    return SemanticResult(True)


def strip_safe_wrappers(argv: tuple[str, ...]) -> tuple[str, ...] | WrapperError:
    """Return the wrapped argv while failing closed on ambiguous wrapper flags."""

    args = tuple(argv)
    while args:
        name = args[0]
        if name in {"time", "nohup"}:
            args = args[1:]
            continue
        if name == "timeout":
            result = _strip_timeout(args)
        elif name == "nice":
            result = _strip_nice(args)
        elif name == "env":
            result = _strip_env(args)
        elif name == "stdbuf":
            result = _strip_stdbuf(args)
        else:
            return args
        if isinstance(result, WrapperError):
            return result
        if result == args:
            return args
        args = result
    return args


def effective_command_name(argv: tuple[str, ...]) -> str | None:
    stripped = strip_safe_wrappers(argv)
    if isinstance(stripped, WrapperError) or not stripped:
        return None
    return stripped[0]


def interpret_exit(
    command_name: str | None,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> ExitInterpretation:
    _ = stdout, stderr
    if exit_code == 0:
        return ExitInterpretation(False)
    if command_name in {"grep", "rg"} and exit_code == 1:
        return ExitInterpretation(False, "No matches found.")
    if command_name == "diff" and exit_code == 1:
        return ExitInterpretation(False, "Files differ.")
    if command_name in {"test", "["} and exit_code == 1:
        return ExitInterpretation(False, "Condition evaluated false.")
    if command_name == "find" and exit_code == 1:
        return ExitInterpretation(False, "find reported partial success.")
    return ExitInterpretation(True, f"Command exited with status {exit_code}.")


def _strip_timeout(args: tuple[str, ...]) -> tuple[str, ...] | WrapperError:
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"--foreground", "--preserve-status", "--verbose", "-v"}:
            index += 1
        elif re.match(r"^--(?:kill-after|signal)=[A-Za-z0-9_.+-]+$", arg):
            index += 1
        elif arg in {"--kill-after", "--signal"} and index + 1 < len(args):
            index += 2
        elif arg in {"-k", "-s"} and index + 1 < len(args):
            index += 2
        elif re.match(r"^-[ks][A-Za-z0-9_.+-]+$", arg):
            index += 1
        elif arg.startswith("-"):
            return WrapperError(f"timeout with {arg} cannot be statically analyzed.")
        else:
            break
    if index >= len(args):
        return args
    if not re.match(r"^\d+(?:\.\d+)?[smhd]?$", args[index]):
        return WrapperError(f"timeout duration '{args[index]}' cannot be statically analyzed.")
    return args[index + 1 :]


def _strip_nice(args: tuple[str, ...]) -> tuple[str, ...] | WrapperError:
    if len(args) >= 3 and args[1] == "-n" and re.match(r"^-?\d+$", args[2]):
        return args[3:]
    if len(args) >= 2 and re.match(r"^-\d+$", args[1]):
        return args[2:]
    if len(args) >= 2 and any(ch in args[1] for ch in "$(`"):
        return WrapperError("nice argument contains runtime expansion.")
    return args[1:] if len(args) > 1 else args


def _strip_env(args: tuple[str, ...]) -> tuple[str, ...] | WrapperError:
    index = 1
    while index < len(args):
        arg = args[index]
        if "=" in arg and not arg.startswith("-"):
            index += 1
        elif arg in {"-i", "-0", "-v"}:
            index += 1
        elif arg == "-u" and index + 1 < len(args):
            index += 2
        elif arg.startswith("-"):
            return WrapperError(f"env with {arg} cannot be statically analyzed.")
        else:
            break
    return args[index:] if index < len(args) else args


def _strip_stdbuf(args: tuple[str, ...]) -> tuple[str, ...] | WrapperError:
    index = 1
    while index < len(args):
        arg = args[index]
        if arg in {"-i", "-o", "-e"} and index + 1 < len(args):
            index += 2
        elif re.match(r"^-[ioe].+", arg) or re.match(r"^--(?:input|output|error)=", arg):
            index += 1
        elif arg.startswith("-"):
            return WrapperError(f"stdbuf with {arg} cannot be statically analyzed.")
        else:
            break
    return args[index:] if index > 1 and index < len(args) else args
