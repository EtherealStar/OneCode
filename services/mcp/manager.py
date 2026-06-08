"""MCP connection lifecycle and tool invocation."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
import os
from pathlib import Path
import tempfile
from threading import Thread
from typing import Any

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from services.mcp.names import build_mcp_tool_name
from services.mcp.results import render_mcp_tool_result
from services.mcp.trust import (
    McpTrustPolicy,
    build_stdio_child_env,
    fingerprint_mcp_server,
)
from services.mcp.types import (
    McpConfigSet,
    McpConnectionSnapshot,
    McpDiscoveredTool,
    McpServerConfig,
    McpServerStatus,
    McpToolCallResult,
)
from services.observability import ErrorLogRecorder, TraceRecorder

STDIO_STDERR_LIMIT_CHARS = 1_000_000
SERVER_INSTRUCTIONS_LIMIT_CHARS = 2_048


@dataclass
class ConnectedMcpServer:
    config: McpServerConfig
    session: ClientSession
    exit_stack: AsyncExitStack
    tools: tuple[McpDiscoveredTool, ...] = ()
    instructions: str | None = None
    stderr: "_LimitedTextLog | None" = None


class McpConnectionManager:
    def __init__(
        self,
        workspace: Path,
        configs: McpConfigSet | dict[str, McpServerConfig],
        *,
        timeout_seconds: float = 30.0,
        max_stdio_concurrency: int = 3,
        max_remote_concurrency: int = 20,
        trace_recorder: TraceRecorder | None = None,
        error_log_recorder: ErrorLogRecorder | None = None,
        trust_policy: McpTrustPolicy | None = None,
    ) -> None:
        self.workspace = workspace.resolve()
        self.configs = configs.servers if isinstance(configs, McpConfigSet) else dict(configs)
        self.timeout_seconds = timeout_seconds
        self.max_stdio_concurrency = max(1, max_stdio_concurrency)
        self.max_remote_concurrency = max(1, max_remote_concurrency)
        self.trace_recorder = trace_recorder or TraceRecorder.noop()
        self.error_log_recorder = error_log_recorder or ErrorLogRecorder.noop()
        self.trust_policy = trust_policy or McpTrustPolicy()
        self._connections: dict[str, ConnectedMcpServer] = {}
        self._statuses: dict[str, McpServerStatus] = {
            name: McpServerStatus(
                name=name,
                transport=config.transport,
                state="pending" if config.enabled else "disabled",
            )
            for name, config in self.configs.items()
        }

    def connect_all_blocking(self) -> McpConnectionSnapshot:
        """Synchronously connect all enabled servers for sync CLI composition."""

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.connect_all())

        result: dict[str, Any] = {}

        def runner() -> None:
            try:
                result["value"] = asyncio.run(self.connect_all())
            except BaseException as exc:
                result["error"] = exc

        thread = Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result["value"]

    async def connect_all(self) -> McpConnectionSnapshot:
        await self.close_all()
        self._statuses = {
            name: McpServerStatus(
                name=name,
                transport=config.transport,
                state="pending" if config.enabled else "disabled",
            )
            for name, config in self.configs.items()
        }
        local_sem = asyncio.Semaphore(self.max_stdio_concurrency)
        remote_sem = asyncio.Semaphore(self.max_remote_concurrency)

        async def connect_one(config: McpServerConfig) -> None:
            if not config.enabled:
                return
            if not self._is_trusted(config):
                self._mark_untrusted(config)
                return
            sem = local_sem if config.transport == "stdio" else remote_sem
            async with sem:
                try:
                    await self._connect_one(config)
                except Exception:
                    return

        await asyncio.gather(*(connect_one(config) for config in self.configs.values()))
        return self.snapshot()

    async def ensure_connected(self, server_name: str) -> ConnectedMcpServer:
        connected = self._connections.get(server_name)
        if connected is not None:
            return connected
        config = self.configs.get(server_name)
        if config is None:
            raise ValueError(f"Unknown MCP server: {server_name}")
        if not config.enabled:
            raise ValueError(f"MCP server is disabled: {server_name}")
        if not self._is_trusted(config):
            self._mark_untrusted(config)
            raise ValueError(f"MCP server is untrusted: {server_name}")
        return await self._connect_one(config)

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
    ) -> McpToolCallResult:
        descriptor_name = build_mcp_tool_name(server_name, tool_name).provider_name
        try:
            connected = await self.ensure_connected(server_name)
            result = await connected.session.call_tool(tool_name, arguments or {})
        except Exception:
            await self._disconnect(server_name)
            try:
                connected = await self.ensure_connected(server_name)
                result = await connected.session.call_tool(tool_name, arguments or {})
            except Exception as exc:
                self.error_log_recorder.record_mcp_error(
                    server_name,
                    exc,
                    attributes={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "stage": "call_tool",
                    },
                )
                self._record_tool_call(
                    server_name,
                    tool_name,
                    descriptor_name,
                    tool_call_id,
                    is_error=True,
                    content_size=0,
                )
                return McpToolCallResult(
                    content=f"MCP tool call failed: {type(exc).__name__}",
                    is_error=True,
                    metadata={
                        "error": "mcp_tool_call_failed",
                        "error_type": type(exc).__name__,
                        "server": server_name,
                        "tool": tool_name,
                    },
                )
        content, metadata, is_error = render_mcp_tool_result(result)
        self._record_tool_call(
            server_name,
            tool_name,
            descriptor_name,
            tool_call_id,
            is_error=is_error,
            content_size=len(content),
        )
        if is_error:
            metadata = {"error": "mcp_tool_error", **metadata}
        metadata.update({"server": server_name, "tool": tool_name})
        return McpToolCallResult(content=content, metadata=metadata, is_error=is_error)

    async def close_all(self) -> None:
        names = tuple(self._connections)
        for name in names:
            await self._disconnect(name)

    def snapshot(self) -> McpConnectionSnapshot:
        statuses = tuple(self._statuses[name] for name in sorted(self._statuses))
        tools = tuple(
            tool
            for name in sorted(self._connections)
            for tool in self._connections[name].tools
        )
        instructions = {
            name: connected.instructions
            for name, connected in sorted(self._connections.items())
            if connected.instructions
        }
        return McpConnectionSnapshot(
            statuses=statuses,
            tools=tools,
            instructions=instructions,
        )

    async def _connect_one(self, config: McpServerConfig) -> ConnectedMcpServer:
        if config.name in self._connections:
            return self._connections[config.name]
        with self.trace_recorder.span(
            "mcp_connect",
            {"server": config.name, "transport": config.transport},
        ) as span:
            exit_stack = AsyncExitStack()
            stderr_log: _LimitedTextLog | None = None
            try:
                streams = await asyncio.wait_for(
                    self._open_streams(config, exit_stack),
                    timeout=self.timeout_seconds,
                )
                if len(streams) < 2:
                    raise RuntimeError("MCP transport did not return read/write streams.")
                session = await exit_stack.enter_async_context(
                    ClientSession(
                        streams[0],
                        streams[1],
                        read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    )
                )
                initialized = await asyncio.wait_for(
                    session.initialize(),
                    timeout=self.timeout_seconds,
                )
                listed = await asyncio.wait_for(
                    session.list_tools(),
                    timeout=self.timeout_seconds,
                )
                tools = _discovered_tools(config.name, getattr(listed, "tools", ()))
                instructions = _truncate_instructions(
                    getattr(initialized, "instructions", None)
                )
                if config.transport == "stdio":
                    stderr_log = getattr(exit_stack, "_onecode_stderr_log", None)
                connected = ConnectedMcpServer(
                    config=config,
                    session=session,
                    exit_stack=exit_stack,
                    tools=tools,
                    instructions=instructions,
                    stderr=stderr_log,
                )
                self._connections[config.name] = connected
                self._statuses[config.name] = McpServerStatus(
                    name=config.name,
                    transport=config.transport,
                    state="connected",
                    tool_count=len(tools),
                    instructions_present=bool(instructions),
                )
                span.end({"status": "connected", "tool_count": len(tools)})
                return connected
            except Exception as exc:
                await exit_stack.aclose()
                self.error_log_recorder.record_mcp_error(
                    config.name,
                    exc,
                    attributes={
                        "transport": config.transport,
                        "stage": "connect",
                    },
                )
                self._statuses[config.name] = McpServerStatus(
                    name=config.name,
                    transport=config.transport,
                    state="failed",
                    error=_error_summary(exc),
                )
                span.end({"status": "failed", "error_type": type(exc).__name__})
                raise

    async def _open_streams(
        self,
        config: McpServerConfig,
        exit_stack: AsyncExitStack,
    ) -> tuple[Any, ...]:
        if config.transport == "stdio":
            env = build_stdio_child_env(dict(os.environ), config)
            stderr_log = _LimitedTextLog(STDIO_STDERR_LIMIT_CHARS)
            setattr(exit_stack, "_onecode_stderr_log", stderr_log)
            exit_stack.callback(stderr_log.close)
            params = StdioServerParameters(
                command=config.command or "",
                args=list(config.args),
                env=env,
                cwd=self.workspace,
            )
            return tuple(await exit_stack.enter_async_context(stdio_client(params, errlog=stderr_log)))
        if config.transport == "sse":
            return tuple(
                await exit_stack.enter_async_context(
                    sse_client(
                        config.url or "",
                        headers=dict(config.headers),
                        timeout=self.timeout_seconds,
                    )
                )
            )
        http_client = await exit_stack.enter_async_context(
            httpx.AsyncClient(
                headers=dict(config.headers),
                timeout=self.timeout_seconds,
            )
        )
        return tuple(
            await exit_stack.enter_async_context(
                streamable_http_client(config.url or "", http_client=http_client)
            )
        )

    def _is_trusted(self, config: McpServerConfig) -> bool:
        return self.trust_policy.is_trusted(config, self.workspace)

    def _mark_untrusted(self, config: McpServerConfig) -> None:
        fingerprint = fingerprint_mcp_server(config, self.workspace)
        self._statuses[config.name] = McpServerStatus(
            name=config.name,
            transport=config.transport,
            state="untrusted",
        )
        self.trace_recorder.event(
            "mcp_connect",
            {
                "server": config.name,
                "transport": config.transport,
                "status": "untrusted",
                "fingerprint": fingerprint,
                "env_key_count": len(config.env),
            },
        )

    async def _disconnect(self, server_name: str) -> None:
        connected = self._connections.pop(server_name, None)
        if connected is None:
            return
        try:
            await connected.exit_stack.aclose()
        except Exception as exc:
            self.error_log_recorder.record_mcp_error(
                server_name,
                exc,
                attributes={"stage": "close"},
            )
            return

    def _record_tool_call(
        self,
        server_name: str,
        tool_name: str,
        descriptor_name: str,
        tool_call_id: str,
        *,
        is_error: bool,
        content_size: int,
    ) -> None:
        self.trace_recorder.event(
            "mcp_tool_call",
            {
                "server": server_name,
                "tool": tool_name,
                "descriptor_name": descriptor_name,
                "tool_call_id": tool_call_id,
                "is_error": is_error,
                "content_size": content_size,
            },
        )


def _discovered_tools(server_name: str, sdk_tools: Any) -> tuple[McpDiscoveredTool, ...]:
    tools: list[McpDiscoveredTool] = []
    for sdk_tool in sdk_tools or ():
        raw_name = getattr(sdk_tool, "name", "")
        if not isinstance(raw_name, str) or not raw_name:
            continue
        tool_name = build_mcp_tool_name(server_name, raw_name)
        tools.append(
            McpDiscoveredTool(
                server_name=server_name,
                normalized_server_name=tool_name.normalized_server,
                tool_name=raw_name,
                normalized_tool_name=tool_name.normalized_tool,
                descriptor_name=tool_name.provider_name,
                description=_string_or_empty(getattr(sdk_tool, "description", "")),
                input_schema=_dict_or_empty(getattr(sdk_tool, "inputSchema", None)),
                annotations=_annotations_dict(getattr(sdk_tool, "annotations", None)),
            )
        )
    return tuple(tools)


def _annotations_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return {
            key: item
            for key, item in dump(exclude_none=True, by_alias=True).items()
            if item is not None
        }
    return {}


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {"type": "object", "properties": {}}


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _truncate_instructions(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:SERVER_INSTRUCTIONS_LIMIT_CHARS]


def _error_summary(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if not message:
        return type(exc).__name__
    return f"{type(exc).__name__}: {message[:200]}"


class _LimitedTextLog:
    def __init__(self, limit_chars: int) -> None:
        self.limit_chars = max(0, limit_chars)
        self._file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    def write(self, value: str) -> int:
        return self._file.write(value)

    def flush(self) -> None:
        self._file.flush()

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        self._file.close()

    def getvalue(self) -> str:
        self._file.flush()
        self._file.seek(0)
        return self._file.read(self.limit_chars)
