"""Model-call context snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PreparedContext:
    messages: tuple[dict[str, Any], ...]
    usage_hints: dict[str, Any] = field(default_factory=dict)
    transcript_refs: tuple[str, ...] = field(default_factory=tuple)

    def __iter__(self):
        return iter(self.messages)


@dataclass(frozen=True)
class ContextSnapshot:
    system_prompt: str
    messages: tuple[dict[str, Any], ...]
    tool_schemas: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    usage_hints: dict[str, Any] = field(default_factory=dict)
    transcript_refs: tuple[str, ...] = field(default_factory=tuple)
    transition: str | None = None
