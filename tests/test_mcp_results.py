from __future__ import annotations

from mcp.types import CallToolResult, ImageContent, TextContent

from services.mcp.results import render_mcp_tool_result


def test_render_mcp_tool_result_concatenates_text_blocks() -> None:
    result = CallToolResult(
        content=(TextContent(type="text", text="first"), TextContent(type="text", text="second")),
        isError=False,
    )

    content, metadata, is_error = render_mcp_tool_result(result)

    assert content == "first\nsecond"
    assert metadata["mcp_is_error"] is False
    assert is_error is False


def test_render_mcp_tool_result_summarizes_images() -> None:
    result = CallToolResult(
        content=(ImageContent(type="image", data="abcd", mimeType="image/png"),),
        isError=True,
    )

    content, metadata, is_error = render_mcp_tool_result(result)

    assert "MCP image content" in content
    assert "image/png" in content
    assert metadata["mcp_is_error"] is True
    assert is_error is True
