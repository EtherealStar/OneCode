from __future__ import annotations

from pathlib import Path

from services.mcp.trust import McpTrustPolicy, McpTrustStore
from services.mcp.types import McpConfigSet, McpServerConfig
from ui.cli.app import (
    _collect_untrusted_project_mcp_servers,
    _prompt_for_project_mcp_trust,
)


def test_mcp_trust_prompt_uses_cli_input_layer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    prompts: list[tuple[str, tuple[str, ...]]] = []

    def fake_read_confirm_sync(title: str, options: tuple[object, ...]) -> str:
        prompts.append((title, tuple(getattr(option, "value") for option in options)))
        return "trust"

    config = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
        args=("server.py",),
    )
    config_set = McpConfigSet(servers={"docs": config})
    trust_store = McpTrustStore(tmp_path / ".onecode" / "settings.json")
    monkeypatch.setattr("ui.cli.app.read_confirm_sync", fake_read_confirm_sync)

    _prompt_for_project_mcp_trust(tmp_path, config_set, trust_store)

    assert prompts == [("Trust this project MCP server?", ("trust", "skip"))]
    assert McpTrustPolicy(trust_store).is_trusted(config, tmp_path) is True


def test_mcp_trust_prompt_skips_on_interrupted_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_read_confirm_sync(title: str, options: tuple[object, ...]) -> str:
        _ = title, options
        raise EOFError

    config = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
    )
    config_set = McpConfigSet(servers={"docs": config})
    trust_store = McpTrustStore(tmp_path / ".onecode" / "settings.json")
    monkeypatch.setattr("ui.cli.app.read_confirm_sync", fake_read_confirm_sync)

    _prompt_for_project_mcp_trust(tmp_path, config_set, trust_store)

    assert McpTrustPolicy(trust_store).is_trusted(config, tmp_path) is False


def test_collect_untrusted_mcp_servers_for_startup_notice(tmp_path: Path) -> None:
    trusted = McpServerConfig(
        name="trusted",
        transport="stdio",
        command="python",
    )
    untrusted = McpServerConfig(
        name="docs",
        transport="stdio",
        command="python",
        args=("server.py",),
        env={"DOCS_TOKEN": "redacted"},
    )
    remote = McpServerConfig(
        name="remote",
        transport="sse",
        url="https://example.invalid/sse",
    )
    config_set = McpConfigSet(
        servers={
            "trusted": trusted,
            "docs": untrusted,
            "remote": remote,
        }
    )
    trust_store = McpTrustStore(tmp_path / ".onecode" / "settings.json")
    from services.mcp.trust import fingerprint_mcp_server

    trust_store.trust_server(
        trusted.name,
        fingerprint_mcp_server(trusted, tmp_path),
        transport=trusted.transport,
    )

    notices = _collect_untrusted_project_mcp_servers(
        tmp_path,
        config_set,
        trust_store,
    )

    assert notices == (
        {
            "name": "docs",
            "command": "python",
            "args": "server.py",
            "cwd": str(tmp_path),
            "explicit_env_keys": "DOCS_TOKEN",
            "base_env_keys": notices[0]["base_env_keys"],
        },
    )
