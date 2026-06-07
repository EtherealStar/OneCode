from __future__ import annotations

from services.mcp.names import build_mcp_tool_name, normalize_mcp_name


def test_normalize_mcp_name_replaces_invalid_characters() -> None:
    assert normalize_mcp_name("docs.search/v1") == "docs_search_v1"
    assert normalize_mcp_name("!!") == "unnamed"


def test_build_mcp_tool_name_prefixes_and_preserves_original_names() -> None:
    name = build_mcp_tool_name("docs.server", "search/docs")

    assert name.provider_name == "mcp__docs_server__search_docs"
    assert name.original_server == "docs.server"
    assert name.original_tool == "search/docs"


def test_build_mcp_tool_name_limits_provider_name_with_stable_hash() -> None:
    first = build_mcp_tool_name("server-" * 20, "tool-" * 30)
    second = build_mcp_tool_name("server-" * 20, "tool-" * 30)

    assert len(first.provider_name) <= 64
    assert first.provider_name == second.provider_name
    assert first.provider_name.startswith("mcp__")
