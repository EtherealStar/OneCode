from __future__ import annotations

from tools.bash.ast_model import BashAnalysis
from tools.bash.parser import parse_bash
from tools.bash.semantics import (
    check_semantics,
    interpret_exit,
    strip_safe_wrappers,
)


def _analysis(command: str) -> BashAnalysis:
    result = parse_bash(command)
    assert isinstance(result, BashAnalysis)
    return result


def test_check_semantics_rejects_unsafe_commands() -> None:
    commands = [
        "eval echo ok",
        "source script.sh",
        ". script.sh",
        "exec rm file",
        "timeout -k 5 10 eval echo ok",
        "command python script.py",
    ]

    for command in commands:
        result = check_semantics(_analysis(command))
        assert result.ok is False


def test_check_semantics_allows_command_lookup() -> None:
    assert check_semantics(_analysis("command -v python")).ok is True


def test_strip_safe_wrappers_returns_effective_command() -> None:
    assert strip_safe_wrappers(("env", "FOO=bar", "timeout", "5", "rg", "x")) == (
        "rg",
        "x",
    )


def test_interpret_exit_special_cases() -> None:
    cases = [
        ("rg", False),
        ("grep", False),
        ("diff", False),
        ("test", False),
        ("python", True),
    ]

    for command, is_error in cases:
        assert interpret_exit(command, 1, "", "").is_error is is_error
