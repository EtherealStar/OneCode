"""MCP integration entry points."""

from services.mcp.config import McpConfigError, load_project_mcp_config
from services.mcp.manager import McpConnectionManager
from services.mcp.names import build_mcp_tool_name, normalize_mcp_name
from services.mcp.tool_factory import build_mcp_tool_descriptors
from services.mcp.types import (
    McpConfigSet,
    McpConnectionSnapshot,
    McpDiscoveredTool,
    McpServerConfig,
    McpServerStatus,
    McpToolCallResult,
    McpToolName,
)

__all__ = [
    "McpConfigError",
    "McpConfigSet",
    "McpConnectionManager",
    "McpConnectionSnapshot",
    "McpDiscoveredTool",
    "McpServerConfig",
    "McpServerStatus",
    "McpToolCallResult",
    "McpToolName",
    "build_mcp_tool_descriptors",
    "build_mcp_tool_name",
    "load_project_mcp_config",
    "normalize_mcp_name",
]
