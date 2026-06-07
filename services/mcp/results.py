"""Convert MCP tool results into OneCode tool result content."""

from __future__ import annotations

from typing import Any


def render_mcp_tool_result(result: Any) -> tuple[str, dict[str, Any], bool]:
    """Return model-visible text, sanitized metadata and error state."""

    is_error = bool(_field(result, "isError", False))
    metadata: dict[str, Any] = {"mcp_is_error": is_error}
    meta = _field(result, "meta", None)
    if isinstance(meta, dict):
        metadata["mcp_meta"] = _sanitize_meta(meta)
    structured = _field(result, "structuredContent", None)
    if structured is not None:
        metadata["structured_content"] = structured

    content_blocks = _field(result, "content", ())
    rendered = [_render_content_block(block) for block in content_blocks or ()]
    content = "\n".join(block for block in rendered if block).strip()
    if not content:
        content = "(MCP tool returned no content.)"
    return content, metadata, is_error


def _render_content_block(block: Any) -> str:
    block_type = _field(block, "type", None)
    if block_type == "text":
        text = _field(block, "text", "")
        return text if isinstance(text, str) else str(text)
    if block_type == "image":
        mime_type = _field(block, "mimeType", "unknown")
        data = _field(block, "data", "")
        size = len(data) if isinstance(data, (str, bytes)) else 0
        return f"[MCP image content: mime_type={mime_type}, encoded_size={size} bytes]"
    if block_type == "resource_link":
        uri = _field(block, "uri", "")
        name = _field(block, "name", "")
        return f"[MCP resource link: {name} {uri}]".strip()
    if block_type == "resource":
        return _render_embedded_resource(block)
    return f"[Unsupported MCP content block: {block_type or type(block).__name__}]"


def _render_embedded_resource(block: Any) -> str:
    resource = _field(block, "resource", None)
    text = _field(resource, "text", None)
    if isinstance(text, str):
        return text
    uri = _field(resource, "uri", "")
    mime_type = _field(resource, "mimeType", "unknown")
    blob = _field(resource, "blob", "")
    size = len(blob) if isinstance(blob, (str, bytes)) else 0
    return f"[MCP binary resource: uri={uri}, mime_type={mime_type}, encoded_size={size} bytes]"


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _sanitize_meta(value: dict[str, Any]) -> dict[str, Any]:
    # Keep MCP metadata structured but shallow-copy it so callers cannot mutate
    # SDK-owned result objects through ToolExecutionResult.metadata.
    return dict(value)
