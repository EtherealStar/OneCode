"""Wrap discovered MCP tools as OneCode ToolDescriptors."""

from __future__ import annotations

from typing import Any

from services.mcp.manager import McpConnectionManager
from services.mcp.types import McpDiscoveredTool
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
)

DESCRIPTION_LIMIT_CHARS = 2_048
MCP_RESULT_POLICY = ToolResultPolicy(
    max_result_size_chars=50_000,
    persist_when_exceeded=True,
    preview_chars=4_000,
)


def build_mcp_tool_descriptors(
    manager: McpConnectionManager,
) -> tuple[ToolDescriptor, ...]:
    return tuple(
        _descriptor_for_tool(manager, tool)
        for tool in manager.snapshot().tools
    )


def _descriptor_for_tool(
    manager: McpConnectionManager,
    tool: McpDiscoveredTool,
) -> ToolDescriptor:
    description = _description(tool)

    async def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
        *,
        _server: str = tool.server_name,
        _tool: str = tool.tool_name,
        _descriptor: str = tool.descriptor_name,
    ) -> ToolExecutionResult:
        result = await manager.call_tool(
            _server,
            _tool,
            dict(tool_input),
            runtime.tool_call_id,
        )
        return ToolExecutionResult(
            tool_call_id=runtime.tool_call_id,
            tool_name=_descriptor,
            content=result.content,
            is_error=result.is_error,
            metadata=result.metadata,
        )

    def classify(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
        *,
        _tool: McpDiscoveredTool = tool,
    ) -> ToolCallClassification:
        del tool_input, runtime
        read_only = (
            _tool.annotations.get("readOnlyHint") is True
            and _tool.annotations.get("destructiveHint") is not True
        )
        return ToolCallClassification(
            read_only=read_only,
            modifies_filesystem=False,
            concurrency_safe=read_only,
            targets=(
                ToolTarget(
                    kind="external_service",
                    operation="call",
                    value=f"{_tool.server_name}/{_tool.tool_name}",
                    metadata={
                        "server": _tool.server_name,
                        "tool": _tool.tool_name,
                    },
                ),
            ),
            result_policy=MCP_RESULT_POLICY,
            permission_subject=f"{_tool.server_name}/{_tool.tool_name}",
        )

    return ToolDescriptor(
        name=tool.descriptor_name,
        description=description,
        input_schema=_input_schema(tool.input_schema),
        handler=handler,
        prompt=(
            "This is an MCP tool backed by an external server. "
            "Pass only arguments described by its input schema and treat errors as external service failures."
        ),
        search_hint=f"MCP external tool {tool.server_name}/{tool.tool_name}",
        classify_input=classify,
    )


def _description(tool: McpDiscoveredTool) -> str:
    base = " ".join((tool.description or "").split())
    if len(base) > DESCRIPTION_LIMIT_CHARS:
        base = base[: DESCRIPTION_LIMIT_CHARS - 3].rstrip() + "..."
    suffix = f"MCP server: {tool.server_name}"
    if not base:
        return suffix
    return f"{base} ({suffix})"


def _input_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(schema, dict) or not schema:
        return {"type": "object", "properties": {}}
    if schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    return dict(schema)
