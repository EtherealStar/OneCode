from __future__ import annotations

from collections.abc import Iterable

from onecode.model_client import ToolCall

from .base import Tool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.meta.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.meta.name}")
        self._tools[tool.meta.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def require(self, name: str) -> Tool:
        tool = self.get(name)
        if not tool:
            raise KeyError(f"Unknown tool: {name}")
        return tool

    def api_schemas(self) -> list[dict]:
        return [tool.meta.api_schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())


def partition_tool_calls(tool_calls: list[ToolCall], registry: ToolRegistry) -> list[list[ToolCall]]:
    """MVP partitioner: keep every call in its own serial batch.

    The interface is intentionally shaped for the future concurrent version,
    where consecutive concurrency-safe calls can be grouped together.
    """

    return [[call] for call in tool_calls]
