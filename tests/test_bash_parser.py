from __future__ import annotations

from tools.bash.ast_model import BashAnalysis, BashParseError
from tools.bash.parser import parse_bash


def test_parse_compound_pipeline_commands() -> None:
    result = parse_bash('git status && rg "foo" . | head -20')

    assert isinstance(result, BashAnalysis)
    assert [command.argv for command in result.commands] == [
        ("git", "status"),
        ("rg", "foo", "."),
        ("head", "-20"),
    ]
    assert result.operators == ("&&", "|")
    assert result.has_pipeline is True


def test_parse_redirects_from_ast() -> None:
    output = parse_bash("echo ok > out.txt")
    input_result = parse_bash("cat < in.txt")

    assert isinstance(output, BashAnalysis)
    assert output.commands[0].redirects[0].op == ">"
    assert output.commands[0].redirects[0].target == "out.txt"
    assert isinstance(input_result, BashAnalysis)
    assert input_result.commands[0].redirects[0].op == "<"
    assert input_result.commands[0].redirects[0].target == "in.txt"


def test_parse_rejects_complex_shell_structures() -> None:
    result = parse_bash("echo $(pwd)")

    assert isinstance(result, BashParseError)
    assert result.kind == "too_complex"


def test_parse_rejects_runtime_expansion_in_word() -> None:
    result = parse_bash("cat $TARGET")

    assert isinstance(result, BashParseError)
    assert result.kind == "too_complex"
