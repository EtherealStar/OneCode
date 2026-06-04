"""Registry for enabled runtime tools."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from services.tools.schema import descriptor_to_openai_tool_schema
from services.tools.types import ToolDescriptor

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState
    from services.permissions import PermissionPolicy


class ToolRegistry:
    def __init__(
        self,
        descriptors: Iterable[ToolDescriptor] = (),
        *,
        disabled_tools: Iterable[str] = (),
        denied_tools: Iterable[str] = (),
        permission_policy: PermissionPolicy | None = None,
    ) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._disabled_tools = {name for name in disabled_tools if name}
        self._denied_tools = {name for name in denied_tools if name}
        self._permission_policy = permission_policy
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

    def visible_descriptors(self, state: RuntimeState) -> tuple[ToolDescriptor, ...]:
        hidden_tools = self._hidden_tool_names(state)
        return tuple(
            descriptor
            for descriptor in self.descriptors()
            if descriptor.name not in hidden_tools
            and (
                self._permission_policy is None
                or self._permission_policy.is_tool_visible(descriptor, state)
            )
        )

    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]:
        return tuple(
            descriptor_to_openai_tool_schema(descriptor)
            for descriptor in self.visible_descriptors(state)
        )

    def tool_prompt_sections(self, state: RuntimeState) -> tuple[str, ...]:
        return tuple(
            descriptor.prompt
            for descriptor in self.visible_descriptors(state)
            if descriptor.prompt.strip()
        )

    def _hidden_tool_names(self, state: RuntimeState) -> set[str]:
        hidden = set(self._disabled_tools)
        hidden.update(self._denied_tools)
        hidden.update(_metadata_tool_names(state, "disabled_tools"))
        hidden.update(_metadata_tool_names(state, "denied_tools"))
        hidden.update(_metadata_tool_names(state, "hidden_tools"))
        return hidden


def _metadata_tool_names(state: RuntimeState, key: str) -> set[str]:
    value = state.metadata.get(key, ())
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    try:
        return {str(item) for item in value if str(item)}
    except TypeError:
        return {str(value)} if str(value) else set()
