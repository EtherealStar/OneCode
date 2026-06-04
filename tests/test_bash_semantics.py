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


def test_check_semantics_rejects_eval_like_commands() -> None:
    for command in ("eval echo ok", "source script.sh", ". script.sh", "exec rm file"):
        result = check_semantics(_analysis(command))
        assert result.ok is False


def test_check_semantics_rejects_wrapper_bypass() -> None:
    result = check_semantics(_analysis("timeout -k 5 10 eval echo ok"))

    assert result.ok is False
    assert "eval" in result.reason


def test_command_v_is_allowed_but_command_execute_is_not() -> None:
    assert check_semantics(_analysis("command -v python")).ok is True
    assert check_semantics(_analysis("command python script.py")).ok is False


def test_strip_safe_wrappers_returns_effective_command() -> None:
    assert strip_safe_wrappers(("env", "FOO=bar", "timeout", "5", "rg", "x")) == (
        "rg",
        "x",
    )


def test_interpret_exit_special_cases() -> None:
    assert interpret_exit("rg", 1, "", "").is_error is False
    assert interpret_exit("grep", 1, "", "").is_error is False
    assert interpret_exit("diff", 1, "", "").is_error is False
    assert interpret_exit("test", 1, "", "").is_error is False
    assert interpret_exit("python", 1, "", "").is_error is True
