"""Project-level .mcp.json loading."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.mcp.types import McpConfigSet, McpServerConfig, McpTransport


class McpConfigError(ValueError):
    """Raised when project MCP configuration is present but invalid."""


def load_project_mcp_config(workspace: Path) -> McpConfigSet:
    path = workspace / ".mcp.json"
    if not path.exists():
        return McpConfigSet()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"Invalid .mcp.json: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise McpConfigError(".mcp.json must contain a JSON object.")
    allowed_root_keys = {"mcpServers"}
    unknown_root_keys = set(payload) - allowed_root_keys
    if unknown_root_keys:
        raise McpConfigError(
            f"Unsupported .mcp.json field: {sorted(unknown_root_keys)[0]}"
        )
    raw_servers = payload.get("mcpServers", {})
    if not isinstance(raw_servers, dict):
        raise McpConfigError(".mcp.json field 'mcpServers' must be an object.")
    servers: dict[str, McpServerConfig] = {}
    for name, raw_config in raw_servers.items():
        if not isinstance(name, str) or not name.strip():
            raise McpConfigError("MCP server names must be non-empty strings.")
        if not isinstance(raw_config, dict):
            raise McpConfigError(f"MCP server '{name}' config must be an object.")
        servers[name] = _parse_server_config(name, raw_config)
    return McpConfigSet(servers=servers)


def _parse_server_config(name: str, raw_config: dict[str, Any]) -> McpServerConfig:
    unsupported = set(raw_config) - {
        "type",
        "enabled",
        "command",
        "args",
        "env",
        "url",
        "headers",
    }
    if unsupported:
        field = sorted(unsupported)[0]
        raise McpConfigError(
            f"MCP server '{name}' uses unsupported field '{field}'. "
            "OneCode MCP v1 supports static stdio, sse and http tools only."
        )
    transport = raw_config.get("type", "stdio")
    if transport not in {"stdio", "sse", "http"}:
        raise McpConfigError(
            f"MCP server '{name}' has unsupported type '{transport}'. "
            "Supported MCP server types are stdio, sse and http."
        )
    enabled = raw_config.get("enabled", True)
    if not isinstance(enabled, bool):
        raise McpConfigError(f"MCP server '{name}' field 'enabled' must be a boolean.")
    parsed_transport: McpTransport = transport
    if parsed_transport == "stdio":
        command = raw_config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise McpConfigError(f"MCP stdio server '{name}' requires command.")
        return McpServerConfig(
            name=name,
            transport=parsed_transport,
            enabled=enabled,
            command=command,
            args=_string_tuple(raw_config.get("args", ()), name, "args"),
            env=_string_map(raw_config.get("env", {}), name, "env"),
        )
    url = raw_config.get("url")
    if not isinstance(url, str) or not url.strip():
        raise McpConfigError(f"MCP {parsed_transport} server '{name}' requires url.")
    return McpServerConfig(
        name=name,
        transport=parsed_transport,
        enabled=enabled,
        url=url,
        headers=_string_map(raw_config.get("headers", {}), name, "headers"),
    )


def _string_tuple(value: Any, server_name: str, field: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise McpConfigError(f"MCP server '{server_name}' field '{field}' must be a list.")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise McpConfigError(
                f"MCP server '{server_name}' field '{field}' must contain strings."
            )
        items.append(item)
    return tuple(items)


def _string_map(value: Any, server_name: str, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise McpConfigError(f"MCP server '{server_name}' field '{field}' must be an object.")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise McpConfigError(
                f"MCP server '{server_name}' field '{field}' must map strings to strings."
            )
        result[key] = item
    return result
