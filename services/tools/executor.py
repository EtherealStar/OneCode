"""Tool executor protocol."""

from __future__ import annotations

from typing import Any, Protocol

from services.tools.types import ToolCall


class ToolExecutor(Protocol):
    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: object,
    ) -> list[dict[str, Any]]:
        ...
