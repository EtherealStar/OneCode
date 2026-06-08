from __future__ import annotations

import json
from pathlib import Path

from services.mcp.trust import (
    McpTrustPolicy,
    McpTrustStore,
    build_stdio_child_env,
    fingerprint_mcp_server,
)
from services.mcp.types import McpServerConfig


def test_mcp_trust_store_records_fingerprint_without_touching_mcp_config(
    tmp_path: Path,
) -> None:
    config = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
        args=("server.py",),
    )
    fingerprint = fingerprint_mcp_server(config, tmp_path)
    store = McpTrustStore(tmp_path / ".onecode" / "settings.json")

    assert McpTrustPolicy(store).is_trusted(config, tmp_path) is False

    store.trust_server("docs", fingerprint)

    assert McpTrustPolicy(store).is_trusted(config, tmp_path) is True
    settings = json.loads((tmp_path / ".onecode" / "settings.json").read_text())
    assert settings["mcp"]["trustedServers"]["docs"]["fingerprint"] == fingerprint
    assert not (tmp_path / ".mcp.json").exists()


def test_mcp_fingerprint_changes_when_execution_config_changes(tmp_path: Path) -> None:
    first = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
        args=("server.py",),
    )
    second = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
        args=("other.py",),
    )

    assert fingerprint_mcp_server(first, tmp_path) != fingerprint_mcp_server(
        second,
        tmp_path,
    )


def test_stdio_child_env_keeps_allowlist_and_explicit_env_only() -> None:
    env = build_stdio_child_env(
        {
            "PATH": "bin",
            "OPENAI_API_KEY": "parent-secret",
            "CUSTOM_SECRET": "parent-custom",
        },
        McpServerConfig(
            name="docs",
            transport="stdio",
            command="python",
            env={"CUSTOM_SECRET": "explicit-custom"},
        ),
    )

    assert env["PATH"] == "bin"
    assert env["CUSTOM_SECRET"] == "explicit-custom"
    assert "OPENAI_API_KEY" not in env
