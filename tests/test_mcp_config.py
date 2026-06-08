from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.mcp.config import McpConfigError, load_project_mcp_config


def test_missing_mcp_config_returns_empty_set(tmp_path: Path) -> None:
    config = load_project_mcp_config(tmp_path)

    assert config.servers == {}


def test_load_project_mcp_config_parses_supported_transports(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "command": "python",
                        "args": ["server.py"],
                        "env": {"DOCS_ROOT": "docs"},
                    },
                    "search": {
                        "type": "http",
                        "url": "https://example.com/mcp",
                        "headers": {"X-Api-Key": "static"},
                    },
                    "legacy": {
                        "type": "sse",
                        "url": "https://example.com/sse",
                        "enabled": False,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    config = load_project_mcp_config(tmp_path)

    assert config.servers["docs"].transport == "stdio"
    assert config.servers["docs"].enabled is True
    assert config.servers["docs"].args == ("server.py",)
    assert config.servers["search"].headers == {"X-Api-Key": "static"}
    assert config.servers["legacy"].enabled is False


@pytest.mark.parametrize(
    "server_config",
    [
        {"type": "sdk"},
        {"type": "ws"},
        {"command": "python", "headersHelper": "helper"},
        {"command": "python", "oauth": True},
    ],
)
def test_load_project_mcp_config_rejects_unsupported_v1_features(
    tmp_path: Path,
    server_config: dict,
) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"bad": server_config}}),
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError):
        load_project_mcp_config(tmp_path)
