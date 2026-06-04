"""Registry for enabled runtime tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from services.tools.schema import descriptor_to_openai_tool_schema
from services.tools.types import ToolDescriptor

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState


class ToolRegistry:
    def __init__(self, descriptors: Iterable[ToolDescriptor] = ()) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: ToolDescriptor) -> None:
        if not descriptor.name:
            raise ValueError("Tool descriptor name must not be empty.")
        if descriptor.name in self._descriptors:
            raise ValueError(f"Tool descriptor already registered: {descriptor.name}")
        self._descriptors[descriptor.name] = descriptor

    def get(self, name: str) -> ToolDescriptor | None:
        return self._descriptors.get(name)

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        # 固定排序让 provider payload 和测试结果稳定，即使工具来自多个
        # 装配入口。
        return tuple(
            self._descriptors[name] for name in sorted(self._descriptors.keys())
        )

    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]:
        return tuple(
            descriptor_to_openai_tool_schema(descriptor)
            for descriptor in self.descriptors()
        )
