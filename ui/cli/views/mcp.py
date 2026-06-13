"""Read-only MCP status view."""

from __future__ import annotations

from collections import defaultdict

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ui.cli.theme import SYMBOLS
from ui.cli.types import CliRuntime
from ui.cli.views.common import preview, titled_section


def render_mcp(runtime: CliRuntime) -> Group:
    if runtime.mcp_manager is None:
        return titled_section(
            "MCP",
            Text(f"{SYMBOLS.info} MCP: disabled", style="onecode.subtle"),
            style="onecode.info",
        )
    snapshot = runtime.mcp_manager.snapshot()
    if not snapshot.statuses:
        return titled_section(
            "MCP",
            Text(f"{SYMBOLS.info} MCP: no servers configured", style="onecode.subtle"),
            style="onecode.info",
        )

    servers = Table(title="Servers", box=None, show_header=True, header_style="onecode.subtle")
    servers.add_column("state", no_wrap=True)
    servers.add_column("name")
    servers.add_column("transport")
    servers.add_column("tools", justify="right")
    servers.add_column("instructions")
    servers.add_column("error")
    for status in snapshot.statuses:
        servers.add_row(
            f"{_state_symbol(status.state)} {status.state}",
            status.name,
            status.transport,
            str(status.tool_count),
            "yes" if status.instructions_present else "no",
            preview(status.error),
        )

    tools = Table(title="Tools", box=None, show_header=True, header_style="onecode.subtle")
    tools.add_column("server")
    tools.add_column("tool")
    tools.add_column("descriptor")
    tools.add_column("description")
    tools.add_column("annotations")
    if snapshot.tools:
        grouped = defaultdict(list)
        for tool in snapshot.tools:
            grouped[tool.server_name].append(tool)
        for server_name in sorted(grouped):
            for tool in sorted(grouped[server_name], key=lambda item: item.tool_name):
                annotations = ", ".join(
                    f"{key}={value}" for key, value in sorted(tool.annotations.items())
                )
                tools.add_row(
                    server_name,
                    tool.tool_name,
                    tool.descriptor_name,
                    preview(tool.description),
                    preview(annotations) if annotations else "none",
                )
    else:
        tools.add_row("none", "", "", "", "")

    return titled_section("MCP", Group(servers, Text(), tools), style="onecode.info")


def _state_symbol(state: str) -> str:
    if state == "connected":
        return SYMBOLS.success
    if state in {"failed", "untrusted"}:
        return SYMBOLS.error
    if state == "disabled":
        return SYMBOLS.warning
    return SYMBOLS.pending
