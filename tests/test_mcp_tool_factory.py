from __future__ import annotations

from services.mcp.types import McpConnectionSnapshot, McpDiscoveredTool
from services.mcp.tool_factory import build_mcp_tool_descriptors
from services.permissions import PermissionPolicy
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall, ToolRuntime
from core.runtime_state import RuntimeState


class FakeMcpManager:
    def __init__(self) -> None:
        self.snapshot_value = McpConnectionSnapshot(
            tools=(
                McpDiscoveredTool(
                    server_name="docs",
                    normalized_server_name="docs",
                    tool_name="search.docs",
                    normalized_tool_name="search_docs",
                    descriptor_name="mcp__docs__search_docs",
                    description="Search docs.",
                    input_schema={"type": "object", "properties": {}},
                    annotations={"readOnlyHint": True},
                ),
                McpDiscoveredTool(
                    server_name="tickets",
                    normalized_server_name="tickets",
                    tool_name="create",
                    normalized_tool_name="create",
                    descriptor_name="mcp__tickets__create",
                    description="Create ticket.",
                    input_schema={"type": "object", "properties": {}},
                    annotations={"destructiveHint": True},
                ),
            )
        )

    def snapshot(self) -> McpConnectionSnapshot:
        return self.snapshot_value


def test_build_mcp_tool_descriptors_classifies_readonly_and_destructive_tools() -> None:
    descriptors = build_mcp_tool_descriptors(FakeMcpManager())  # type: ignore[arg-type]
    by_name = {descriptor.name: descriptor for descriptor in descriptors}
    state = RuntimeState()

    read_only = by_name["mcp__docs__search_docs"].classify_input(
        {},
        ToolRuntime(state=state),
    )
    destructive = by_name["mcp__tickets__create"].classify_input(
        {},
        ToolRuntime(state=state),
    )

    assert read_only.read_only is True
    assert read_only.concurrency_safe is True
    assert read_only.targets[0].kind == "external_service"
    assert destructive.read_only is False
    assert destructive.concurrency_safe is False


def test_permission_policy_asks_for_non_readonly_mcp_tools() -> None:
    descriptors = build_mcp_tool_descriptors(FakeMcpManager())  # type: ignore[arg-type]
    descriptor = next(
        item for item in descriptors if item.name == "mcp__tickets__create"
    )
    state = RuntimeState()
    classification = descriptor.classify_input({}, ToolRuntime(state=state))
    policy = PermissionPolicy()

    decision = policy.evaluate(
        tool_call=ToolCall(id="call-1", name=descriptor.name, input={}),
        descriptor=descriptor,
        classification=classification,
        guard_policies=(),
        state=state,
    )

    assert decision.action == "ask"
    assert "MCP tool may change external service state" in decision.reason


def test_project_deny_hides_mcp_tool_from_registry() -> None:
    descriptors = build_mcp_tool_descriptors(FakeMcpManager())  # type: ignore[arg-type]
    state = RuntimeState()
    state.metadata["denied_tools"] = {"mcp__docs__search_docs"}
    registry = ToolRegistry(descriptors, permission_policy=PermissionPolicy())

    visible = [descriptor.name for descriptor in registry.visible_descriptors(state)]

    assert "mcp__docs__search_docs" not in visible
    assert "mcp__tickets__create" in visible
