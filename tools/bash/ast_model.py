"""Stable Bash AST analysis models used by the tool runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EnvVar:
    name: str
    value: str


@dataclass(frozen=True)
class Redirect:
    op: Literal[">", ">>", "<", "<<", ">&", ">|", "<&", "&>", "&>>", "<<<"]
    target: str
    fd: int | None = None


@dataclass(frozen=True)
class SimpleCommand:
    argv: tuple[str, ...]
    env_vars: tuple[EnvVar, ...]
    redirects: tuple[Redirect, ...]
    text: str


@dataclass(frozen=True)
class BashAnalysis:
    commands: tuple[SimpleCommand, ...]
    operators: tuple[str, ...]
    has_pipeline: bool
    has_cd: bool


@dataclass(frozen=True)
class BashParseError:
    kind: Literal["parse_unavailable", "too_complex"]
    reason: str
    node_type: str | None = None
