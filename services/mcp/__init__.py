"""MCP integration entry points."""

from services.mcp.config import McpConfigError, load_project_mcp_config
from services.mcp.manager import McpConnectionManager
from services.mcp.names import build_mcp_tool_name, normalize_mcp_name
from services.mcp.tool_factory import build_mcp_tool_descriptors
from services.mcp.trust import (
    BASE_STDIO_ENV_ALLOWLIST,
    McpTrustPolicy,
    McpTrustStore,
    build_stdio_child_env,
    fingerprint_mcp_server,
)
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
    "McpTrustPolicy",
    "McpTrustStore",
    "BASE_STDIO_ENV_ALLOWLIST",
    "build_stdio_child_env",
    "build_mcp_tool_descriptors",
    "build_mcp_tool_name",
    "fingerprint_mcp_server",
    "load_project_mcp_config",
    "normalize_mcp_name",
]
