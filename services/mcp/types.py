"""Stable OneCode-side MCP data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


McpTransport = Literal["stdio", "sse", "http"]
McpServerState = Literal["pending", "connected", "failed", "disabled", "untrusted"]


@dataclass(frozen=True)
class McpServerConfig:
    name: str
    transport: McpTransport
    enabled: bool = True
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpConfigSet:
    servers: dict[str, McpServerConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolName:
    provider_name: str
    normalized_server: str
    normalized_tool: str
    original_server: str
    original_tool: str


@dataclass(frozen=True)
class McpDiscoveredTool:
    server_name: str
    normalized_server_name: str
    tool_name: str
    normalized_tool_name: str
    descriptor_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpServerStatus:
    name: str
    transport: McpTransport
    state: McpServerState
    tool_count: int = 0
    error: str | None = None
    instructions_present: bool = False


@dataclass(frozen=True)
class McpConnectionSnapshot:
    statuses: tuple[McpServerStatus, ...] = ()
    tools: tuple[McpDiscoveredTool, ...] = ()
    instructions: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpToolCallResult:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
