"""Tree-sitter backed Bash parser.

The parser deliberately exposes only OneCode dataclasses. Raw tree-sitter
nodes stay private so downstream code cannot start depending on grammar
internals or accidentally bypass the fail-closed walker.
"""

from __future__ import annotations

import re
from typing import Any

from tree_sitter import Language, Parser
import tree_sitter_bash

from tools.bash.ast_model import BashAnalysis, BashParseError, EnvVar, Redirect, SimpleCommand


STRUCTURAL_TYPES = {"program", "list", "pipeline", "redirected_statement"}
SEPARATOR_TYPES = {"&&", "||", "|", ";", "\n"}
UNSUPPORTED_TYPES = {
    "subshell",
    "compound_statement",
    "for_statement",
    "while_statement",
    "until_statement",
    "if_statement",
    "case_statement",
    "function_definition",
    "command_substitution",
    "process_substitution",
    "expansion",
    "simple_expansion",
    "brace_expression",
    "heredoc_redirect",
    "herestring_redirect",
    "test_command",
    "ERROR",
}
REDIRECT_OPS = {">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"}
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
UNICODE_WHITESPACE_RE = re.compile(
    r"[\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff]"
)
BACKSLASH_WHITESPACE_RE = re.compile(r"\\[ \t]|[^ \t\n\\]\\\n")
BRACE_EXPANSION_RE = re.compile(r"\{[^{}\s]*(,|\.\.)[^{}\s]*\}")

_PARSER: Parser | None = None


def parse_bash(command: str) -> BashAnalysis | BashParseError:
    """Parse a command into simple commands or return a conservative error."""

    precheck = _precheck(command)
    if precheck is not None:
        return precheck
    if command.strip() == "":
        return BashAnalysis(commands=(), operators=(), has_pipeline=False, has_cd=False)

    parser = _parser()
    source_bytes = command.encode("utf-8")
    tree = parser.parse(source_bytes)
    commands: list[SimpleCommand] = []
    operators: list[str] = []
    error = _collect(tree.root_node, source_bytes, commands, operators)
    if error is not None:
        return error
    return BashAnalysis(
        commands=tuple(commands),
        operators=tuple(operators),
        has_pipeline="|" in operators,
        has_cd=any(command.argv[:1] == ("cd",) for command in commands),
    )


def _parser() -> Parser:
    global _PARSER
    if _PARSER is not None:
        return _PARSER
    parser = Parser()
    language_value = tree_sitter_bash.language()
    # tree-sitter-bash returns a PyCapsule with current bindings; older
    # bindings accepted Language directly. Wrapping keeps this code explicit.
    language = Language(language_value)
    if hasattr(parser, "set_language"):
        parser.set_language(language)
    else:
        parser.language = language
    _PARSER = parser
    return parser


def _precheck(command: str) -> BashParseError | None:
    if CONTROL_CHAR_RE.search(command):
        return BashParseError("too_complex", "Contains control characters")
    if UNICODE_WHITESPACE_RE.search(command):
        return BashParseError("too_complex", "Contains Unicode whitespace")
    if BACKSLASH_WHITESPACE_RE.search(command):
        return BashParseError("too_complex", "Contains backslash-escaped whitespace")
    return None


def _collect(
    node: Any,
    source: bytes,
    commands: list[SimpleCommand],
    operators: list[str],
) -> BashParseError | None:
    if node.type in UNSUPPORTED_TYPES:
        return _too_complex(node)
    if node.type == "command":
        parsed = _command(node, source)
        if isinstance(parsed, BashParseError):
            return parsed
        commands.append(parsed)
        return None
    if node.type == "redirected_statement":
        return _collect_redirected_statement(node, source, commands, operators)
    if node.type not in STRUCTURAL_TYPES:
        if node.is_named:
            return _too_complex(node)
        if node.type in SEPARATOR_TYPES:
            operators.append(node.type)
        return None
    for child in node.children:
        if child.type in SEPARATOR_TYPES:
            operators.append(child.type)
            continue
        error = _collect(child, source, commands, operators)
        if error is not None:
            return error
    return None


def _collect_redirected_statement(
    node: Any,
    source: bytes,
    commands: list[SimpleCommand],
    operators: list[str],
) -> BashParseError | None:
    before = len(commands)
    redirects: list[Redirect] = []
    for child in node.children:
        if child.type == "command":
            parsed = _command(child, source)
            if isinstance(parsed, BashParseError):
                return parsed
            commands.append(parsed)
            continue
        if child.type == "file_redirect":
            redirect = _redirect(child, source)
            if isinstance(redirect, BashParseError):
                return redirect
            redirects.append(redirect)
            continue
        if child.type in SEPARATOR_TYPES:
            operators.append(child.type)
            continue
        if child.is_named:
            return _too_complex(child)
    if redirects:
        if len(commands) != before + 1:
            return BashParseError(
                "too_complex",
                "Redirected statement does not wrap exactly one command",
                node.type,
            )
        command = commands[-1]
        commands[-1] = SimpleCommand(
            argv=command.argv,
            env_vars=command.env_vars,
            redirects=(*command.redirects, *redirects),
            text=_text(node, source),
        )
    return None


def _command(node: Any, source: bytes) -> SimpleCommand | BashParseError:
    argv: list[str] = []
    env_vars: list[EnvVar] = []
    redirects: list[Redirect] = []
    for child in node.children:
        if child.type == "variable_assignment":
            env_var = _env_var(child, source)
            if isinstance(env_var, BashParseError):
                return env_var
            env_vars.append(env_var)
            continue
        if child.type in {
            "command_name",
            "word",
            "number",
            "string",
            "raw_string",
            "concatenation",
        }:
            value = _argument(child, source)
            if isinstance(value, BashParseError):
                return value
            if BRACE_EXPANSION_RE.search(value):
                return BashParseError("too_complex", "Contains brace expansion", child.type)
            argv.append(value)
            continue
        if child.type in {"file_redirect", "redirected_statement"}:
            redirect = _redirect(child, source)
            if isinstance(redirect, BashParseError):
                return redirect
            redirects.append(redirect)
            continue
        if child.type in REDIRECT_OPS or not child.is_named:
            continue
        return _too_complex(child)
    return SimpleCommand(
        argv=tuple(argv),
        env_vars=tuple(env_vars),
        redirects=tuple(redirects),
        text=_text(node, source),
    )


def _env_var(node: Any, source: bytes) -> EnvVar | BashParseError:
    text = _text(node, source)
    if "=" not in text:
        return BashParseError("too_complex", "Unsupported variable assignment", node.type)
    name, value = text.split("=", 1)
    if not name:
        return BashParseError("too_complex", "Empty variable assignment name", node.type)
    return EnvVar(name=name, value=_strip_static_quotes(value))


def _redirect(node: Any, source: bytes) -> Redirect | BashParseError:
    op: str | None = None
    target: str | None = None
    fd: int | None = None
    for child in node.children:
        text = _text(child, source)
        if child.type in REDIRECT_OPS:
            op = child.type
            continue
        if child.type == "file_descriptor":
            try:
                fd = int(text)
            except ValueError:
                return BashParseError("too_complex", "Unsupported redirect fd", child.type)
            continue
        if child.type in {
            "word",
            "number",
            "string",
            "raw_string",
            "concatenation",
        }:
            value = _argument(child, source)
            if isinstance(value, BashParseError):
                return value
            target = value
            continue
        if child.is_named:
            return _too_complex(child)
    if op in {"<<", "<<<"}:
        return BashParseError("too_complex", "Heredoc/herestring redirects are unsupported", op)
    if op is None:
        return BashParseError("too_complex", "Redirect operator is missing", node.type)
    if target is None:
        if op in {">&", "<&"}:
            target = ""
        else:
            return BashParseError("too_complex", "Redirect target is missing", node.type)
    return Redirect(op=op, target=target, fd=fd)  # type: ignore[arg-type]


def _argument(node: Any, source: bytes) -> str | BashParseError:
    if node.type in UNSUPPORTED_TYPES:
        return _too_complex(node)
    if node.type == "command_name":
        named = [child for child in node.children if child.is_named]
        if len(named) != 1:
            return BashParseError("too_complex", "Unsupported command name", node.type)
        return _argument(named[0], source)
    if node.type in {"word", "number"}:
        text = _text(node, source)
        if any(ch in text for ch in "$`"):
            return BashParseError("too_complex", "Runtime expansion is unsupported", node.type)
        return _unescape_word(text)
    if node.type == "raw_string":
        return _strip_raw_string(_text(node, source))
    if node.type == "string":
        return _string(node, source)
    if node.type == "concatenation":
        parts: list[str] = []
        for child in node.children:
            value = _argument(child, source)
            if isinstance(value, BashParseError):
                return value
            parts.append(value)
        return "".join(parts)
    return _too_complex(node)


def _string(node: Any, source: bytes) -> str | BashParseError:
    value = _text(node, source)
    if any(ch in value for ch in "$`"):
        return BashParseError("too_complex", "Runtime expansion is unsupported", node.type)
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return _unescape_double(value[1:-1])
    return _strip_static_quotes(value)


def _strip_static_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _strip_raw_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1]
    return value


def _unescape_word(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] == "\\" and index + 1 < len(value):
            result.append(value[index + 1])
            index += 2
            continue
        result.append(value[index])
        index += 1
    return "".join(result)


def _unescape_double(value: str) -> str:
    return (
        value.replace(r"\"", '"')
        .replace(r"\\", "\\")
        .replace(r"\$", "$")
        .replace(r"\`", "`")
    )


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _too_complex(node: Any) -> BashParseError:
    return BashParseError(
        "too_complex",
        f"Unsupported shell structure: {node.type}",
        node.type,
    )
