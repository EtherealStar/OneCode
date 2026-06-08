from __future__ import annotations

from tools.bash.ast_model import BashAnalysis, BashParseError
from tools.bash.parser import parse_bash


def test_parse_supported_bash_subset() -> None:
    cases = [
        (
            'git status && rg "foo" . | head -20',
            [("git", "status"), ("rg", "foo", "."), ("head", "-20")],
            ("&&", "|"),
            True,
            None,
        ),
        ("echo ok > out.txt", [("echo", "ok")], (), False, (">", "out.txt")),
        ("cat < in.txt", [("cat",)], (), False, ("<", "in.txt")),
    ]

    for command, argvs, operators, has_pipeline, redirect in cases:
        result = parse_bash(command)

        assert isinstance(result, BashAnalysis)
        assert [parsed_command.argv for parsed_command in result.commands] == argvs
        assert result.operators == operators
        assert result.has_pipeline is has_pipeline
        if redirect is not None:
            assert result.commands[0].redirects[0].op == redirect[0]
            assert result.commands[0].redirects[0].target == redirect[1]


def test_parse_rejects_unsupported_runtime_structures() -> None:
    for command in ["echo $(pwd)", "cat $TARGET"]:
        result = parse_bash(command)

        assert isinstance(result, BashParseError)
        assert result.kind == "too_complex"
