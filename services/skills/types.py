"""Domain types for OneCode skill loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


SkillSource = Literal["bundled", "user", "project"]
SkillContext = Literal["inline", "fork"]


@dataclass(frozen=True)
class SkillCommand:
    name: str
    description: str
    content: str
    source: SkillSource
    root: Path | None = None
    when_to_use: str | None = None
    allowed_tools: tuple[str, ...] = ()
    context: SkillContext = "inline"
    model: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    paths: tuple[str, ...] = ()
    frontmatter_keys: frozenset[str] = frozenset()
