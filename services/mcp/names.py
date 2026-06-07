"""MCP name normalization for provider-visible tool names."""

from __future__ import annotations

from hashlib import sha256
import re

from services.mcp.types import McpToolName

MAX_PROVIDER_TOOL_NAME_CHARS = 64
_DISALLOWED_CHARS_RE = re.compile(r"[^a-zA-Z0-9_-]")


def normalize_mcp_name(name: str, *, max_length: int = MAX_PROVIDER_TOOL_NAME_CHARS) -> str:
    """Return a provider-safe MCP name component.

    Tool providers commonly restrict function names to letters, numbers,
    underscores and hyphens. Empty or all-invalid names collapse to ``unnamed``.
    Long names keep a stable hash suffix so truncation does not silently collide.
    """

    normalized = _sanitize_component(name)
    if len(normalized) <= max_length:
        return normalized
    suffix = "_" + sha256(name.encode("utf-8")).hexdigest()[:8]
    budget = max(1, max_length - len(suffix))
    return normalized[:budget].rstrip("_-")[:budget] + suffix


def build_mcp_tool_name(server_name: str, tool_name: str) -> McpToolName:
    normalized_server = normalize_mcp_name(server_name)
    normalized_tool = normalize_mcp_name(tool_name)
    provider_name = f"mcp__{normalized_server}__{normalized_tool}"
    if len(provider_name) > MAX_PROVIDER_TOOL_NAME_CHARS:
        provider_name, normalized_server, normalized_tool = _shorten_prefixed_name(
            server_name,
            tool_name,
            _sanitize_component(server_name),
            _sanitize_component(tool_name),
        )
    return McpToolName(
        provider_name=provider_name,
        normalized_server=normalized_server,
        normalized_tool=normalized_tool,
        original_server=server_name,
        original_tool=tool_name,
    )


def _sanitize_component(name: str) -> str:
    normalized = _DISALLOWED_CHARS_RE.sub("_", str(name).strip())
    normalized = normalized.strip("_-")
    return normalized or "unnamed"


def _shorten_prefixed_name(
    server_name: str,
    tool_name: str,
    server_component: str,
    tool_component: str,
) -> tuple[str, str, str]:
    suffix = "_" + sha256(f"{server_name}\0{tool_name}".encode("utf-8")).hexdigest()[:8]
    fixed_chars = len("mcp__") + len("__") + len(suffix)
    budget = MAX_PROVIDER_TOOL_NAME_CHARS - fixed_chars
    server_budget = min(len(server_component), max(1, min(24, budget // 3)))
    tool_budget = max(1, budget - server_budget)
    if len(tool_component) < tool_budget:
        server_budget = min(len(server_component), budget - len(tool_component))
        tool_budget = budget - server_budget
    elif len(server_component) < server_budget:
        tool_budget = budget - len(server_component)
        server_budget = len(server_component)
    short_server = server_component[:server_budget].rstrip("_-") or "s"
    short_tool = tool_component[:tool_budget].rstrip("_-") or "t"
    provider_name = f"mcp__{short_server}__{short_tool}{suffix}"
    return provider_name, short_server, short_tool
