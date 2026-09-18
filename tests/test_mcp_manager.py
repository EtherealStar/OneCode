from __future__ import annotations

import asyncio
from contextlib import contextmanager
import socket
import sys
import time
from pathlib import Path
from threading import Thread
from typing import Any, Iterator

import uvicorn

from services.mcp.manager import McpConnectionManager
from services.mcp.trust import McpTrustPolicy
from services.mcp.types import McpConfigSet, McpServerConfig


def test_mcp_connection_manager_discovers_and_calls_stdio_tools(tmp_path: Path) -> None:
    server_path = tmp_path / "fake_mcp_server.py"
    server_path.write_text(
        "\n".join(
            [
                "from mcp.server.fastmcp import FastMCP",
                "from mcp.types import ToolAnnotations",
                "mcp = FastMCP('fake', instructions='Use fake MCP instructions.')",
                "@mcp.tool(name='search.docs', description='Search docs.', annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))",
                "def search_docs(query: str) -> str:",
                "    return 'result:' + query",
                "if __name__ == '__main__':",
                "    mcp.run('stdio')",
            ]
        ),
        encoding="utf-8",
    )
    manager = McpConnectionManager(
        tmp_path,
        McpConfigSet(
            {
                "docs": McpServerConfig(
                    name="docs",
                    transport="stdio",
                    command=sys.executable,
                    args=(str(server_path),),
                )
            }
        ),
        timeout_seconds=10,
        trust_policy=McpTrustPolicy.trust_all_servers(),
    )

    async def scenario() -> None:
        snapshot = await manager.connect_all()
        assert snapshot.statuses[0].state == "connected", snapshot.statuses[0]
        assert snapshot.statuses[0].tool_count == 1
        assert snapshot.instructions["docs"] == "Use fake MCP instructions."
        assert snapshot.tools[0].descriptor_name == "mcp__docs__search_docs"
        result = await manager.call_tool(
            "docs",
            "search.docs",
            {"query": "runtime"},
            "call-1",
        )
        assert result.is_error is False
        assert result.content == "result:runtime"
        await manager.close_all()

    asyncio.run(scenario())


def test_mcp_connection_manager_discovers_and_calls_sse_tools(tmp_path: Path) -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fake-sse", instructions="Use SSE instructions.")

    @mcp.tool(name="lookup.docs", description="Lookup docs.")
    def lookup_docs(query: str) -> str:
        return "sse:" + query

    with _serve_asgi_app(mcp.sse_app()) as base_url:
        manager = McpConnectionManager(
            tmp_path,
            McpConfigSet(
                {
                    "docs": McpServerConfig(
                        name="docs",
                        transport="sse",
                        url=f"{base_url}/sse",
                    )
                }
            ),
            timeout_seconds=10,
        )

        async def scenario() -> None:
            snapshot = await manager.connect_all()
            assert snapshot.statuses[0].state == "connected", snapshot.statuses[0]
            assert snapshot.statuses[0].tool_count == 1
            assert snapshot.instructions["docs"] == "Use SSE instructions."
            assert snapshot.tools[0].descriptor_name == "mcp__docs__lookup_docs"
            result = await manager.call_tool(
                "docs",
                "lookup.docs",
                {"query": "runtime"},
                "call-sse",
            )
            assert result.is_error is False
            assert result.content == "sse:runtime"
            await manager.close_all()

        asyncio.run(scenario())


def test_mcp_connection_manager_leaves_untrusted_stdio_pending(
    tmp_path: Path,
) -> None:
    manager = _OpeningTrackingMcpConnectionManager(
        tmp_path,
        McpConfigSet(
            {
                "docs": McpServerConfig(
                    name="docs",
                    transport="stdio",
                    command=sys.executable,
                )
            }
        ),
        trust_policy=McpTrustPolicy(),
    )

    async def scenario() -> None:
        snapshot = await manager.connect_all()
        assert snapshot.statuses[0].state == "untrusted"
        assert snapshot.tools == ()
        assert manager.open_attempts == 0

    asyncio.run(scenario())


def test_mcp_connection_manager_does_not_lazy_connect_untrusted_stdio(
    tmp_path: Path,
) -> None:
    manager = _OpeningTrackingMcpConnectionManager(
        tmp_path,
        McpConfigSet(
            {
                "docs": McpServerConfig(
                    name="docs",
                    transport="stdio",
                    command=sys.executable,
                )
            }
        ),
        trust_policy=McpTrustPolicy(),
    )

    async def scenario() -> None:
        try:
            await manager.ensure_connected("docs")
        except ValueError as exc:
            assert "untrusted" in str(exc)
        else:
            raise AssertionError("untrusted stdio server should not connect")
        assert manager.snapshot().statuses[0].state == "untrusted"
        assert manager.open_attempts == 0

    asyncio.run(scenario())


def test_mcp_stdio_env_uses_allowlist_and_explicit_env(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "parent-secret")
    server_path = tmp_path / "env_mcp_server.py"
    server_path.write_text(
        "\n".join(
            [
                "import os",
                "from mcp.server.fastmcp import FastMCP",
                "mcp = FastMCP('env')",
                "@mcp.tool(name='env.check')",
                "def env_check() -> str:",
                "    secret = os.environ.get('OPENAI_API_KEY', 'missing')",
                "    explicit = os.environ.get('ONECODE_EXPLICIT', 'missing')",
                "    return secret + '|' + explicit",
                "if __name__ == '__main__':",
                "    mcp.run('stdio')",
            ]
        ),
        encoding="utf-8",
    )
    manager = McpConnectionManager(
        tmp_path,
        McpConfigSet(
            {
                "env": McpServerConfig(
                    name="env",
                    transport="stdio",
                    command=sys.executable,
                    args=(str(server_path),),
                    env={"ONECODE_EXPLICIT": "explicit-value"},
                )
            }
        ),
        timeout_seconds=10,
        trust_policy=McpTrustPolicy.trust_all_servers(),
    )

    async def scenario() -> None:
        await manager.connect_all()
        result = await manager.call_tool("env", "env.check", {}, "call-env")
        assert result.content == "missing|explicit-value"
        await manager.close_all()

    asyncio.run(scenario())


def test_mcp_connection_manager_discovers_and_calls_streamable_http_tools(
    tmp_path: Path,
) -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("fake-http", instructions="Use HTTP instructions.")

    @mcp.tool(name="lookup.docs", description="Lookup docs.")
    def lookup_docs(query: str) -> str:
        return "http:" + query

    with _serve_asgi_app(mcp.streamable_http_app()) as base_url:
        manager = McpConnectionManager(
            tmp_path,
            McpConfigSet(
                {
                    "docs": McpServerConfig(
                        name="docs",
                        transport="http",
                        url=f"{base_url}/mcp",
                    )
                }
            ),
            timeout_seconds=10,
        )

        async def scenario() -> None:
            snapshot = await manager.connect_all()
            assert snapshot.statuses[0].state == "connected", snapshot.statuses[0]
            assert snapshot.statuses[0].tool_count == 1
            assert snapshot.instructions["docs"] == "Use HTTP instructions."
            assert snapshot.tools[0].descriptor_name == "mcp__docs__lookup_docs"
            result = await manager.call_tool(
                "docs",
                "lookup.docs",
                {"query": "runtime"},
                "call-http",
            )
            assert result.is_error is False
            assert result.content == "http:runtime"
            await manager.close_all()

        asyncio.run(scenario())


def test_mcp_connection_manager_reconnects_once_after_call_failure(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "crashed_once"
    server_path = tmp_path / "flaky_mcp_server.py"
    server_path.write_text(
        "\n".join(
            [
                "import os",
                "import sys",
                "from mcp.server.fastmcp import FastMCP",
                "marker = sys.argv[1]",
                "mcp = FastMCP('flaky')",
                "@mcp.tool(name='lookup.docs')",
                "def lookup_docs(query: str) -> str:",
                "    if not os.path.exists(marker):",
                "        open(marker, 'w').close()",
                "        os._exit(1)",
                "    return 'reconnected:' + query",
                "if __name__ == '__main__':",
                "    mcp.run('stdio')",
            ]
        ),
        encoding="utf-8",
    )
    manager = McpConnectionManager(
        tmp_path,
        McpConfigSet(
            {
                "docs": McpServerConfig(
                    name="docs",
                    transport="stdio",
                    command=sys.executable,
                    args=(str(server_path), str(marker)),
                )
            }
        ),
        timeout_seconds=10,
        trust_policy=McpTrustPolicy.trust_all_servers(),
    )

    async def scenario() -> None:
        result = await manager.call_tool(
            "docs",
            "lookup.docs",
            {"query": "runtime"},
            "call-retry",
        )
        assert result.is_error is False
        assert result.content == "reconnected:runtime"
        await manager.close_all()

    asyncio.run(scenario())


@contextmanager
def _serve_asgi_app(app: Any) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="critical",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = Thread(target=run, daemon=True)
    thread.start()
    try:
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("uvicorn server failed to start")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("uvicorn server failed to stop")


class _OpeningTrackingMcpConnectionManager(McpConnectionManager):
    def __init__(
        self,
        workspace: Path,
        configs: McpConfigSet,
        *,
        trust_policy: McpTrustPolicy,
    ) -> None:
        super().__init__(workspace, configs, trust_policy=trust_policy)
        self.open_attempts = 0

    async def _open_streams(self, config: McpServerConfig, exit_stack: Any) -> tuple[Any, ...]:
        del config, exit_stack
        self.open_attempts += 1
        raise AssertionError("untrusted stdio server should not open streams")

