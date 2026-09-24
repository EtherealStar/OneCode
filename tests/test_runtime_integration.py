from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.catalog import get_provider_definition
from sdk_test_support import (
    SSE_HEADERS,
    async_sdk,
    text_chunk,
    tool_call_chunk,
)
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor


class SequencedSdkHandler:
    """为每个请求返回一段 SSE 流，并记录出站请求体。"""

    def __init__(self, turns: list[list[dict[str, Any]]]) -> None:
        self._turns = list(turns)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._turns:
            raise AssertionError("Unexpected provider call")
        chunks = self._turns.pop(0)
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(
            200,
            content=body + "data: [DONE]\n\n",
            headers=SSE_HEADERS,
        )

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def tool_call_turn(call_id: str, name: str, arguments: str) -> list[dict[str, Any]]:
    return [
        tool_call_chunk(
            0,
            call_id=call_id,
            name=name,
            arguments=arguments,
            finish_reason="tool_calls",
        )
    ]


def text_turn(content: str) -> list[dict[str, Any]]:
    return [text_chunk(content, finish_reason="stop")]


def make_config() -> ResolvedProviderConfig:
    provider = get_provider_definition("openai")
    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        "https://api.openai.com/v1",
        "gpt-test",
        "secret",
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


def make_loop(
    workspace: Path,
    handler: SequencedSdkHandler,
) -> tuple[AgentLoop, ToolRegistry, Any]:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=workspace / ".onecode",
        session_id=state.session_id,
        cwd=workspace,
        flush_interval_seconds=60,
    )
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor()])
    context_engine = ContextEngine(message_store, tool_schema_provider=registry)
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    config = make_config()
    sdk = async_sdk(config, handler)
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=context_engine,
        model_client=OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk),
        tool_executor=RegistryToolExecutor(registry, guard=guard),
    )
    return loop, registry, sdk


def run_to_final_text(loop: AgentLoop, prompt: str, sdk: Any | None = None) -> str:
    async def run() -> str:
        final_text = ""
        async for event in loop.stream(prompt):
            if event.type == "completed":
                final_text = event.text
        return final_text

    try:
        return asyncio.run(run())
    finally:
        if sdk is not None:
            asyncio.run(sdk.close())


def test_provider_loop_can_read_file_with_registry_executor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    handler = SequencedSdkHandler(
        [
            tool_call_turn("call_read", "read_file", '{"file_path":"a.txt"}'),
            text_turn("read complete"),
        ]
    )
    loop, registry, sdk = make_loop(workspace, handler)

    result = run_to_final_text(loop, "inspect a.txt", sdk)

    assert result == "read complete"
    assert len(handler.bodies) == 2
    first_payload = handler.bodies[0]
    assert first_payload["tools"] == list(registry.tool_schemas(loop.state))
    second_messages = handler.bodies[1]["messages"]
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_read",
        "content": "1\tone\n2\ttwo",
    }


def test_provider_loop_can_read_then_edit_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "a.txt"
    target.write_text("hello old world", encoding="utf-8")
    handler = SequencedSdkHandler(
        [
            tool_call_turn("call_read", "read_file", '{"file_path":"a.txt"}'),
            tool_call_turn(
                "call_edit",
                "edit_file",
                (
                    '{"file_path":"a.txt","old_string":"old",'
                    '"new_string":"new"}'
                ),
            ),
            text_turn("edit complete"),
        ]
    )
    loop, _registry, sdk = make_loop(workspace, handler)

    result = run_to_final_text(loop, "change a.txt", sdk)

    assert result == "edit complete"
    assert target.read_text(encoding="utf-8") == "hello new world"
    assert len(handler.bodies) == 3
    edit_payload_messages = handler.bodies[2]["messages"]
    assert edit_payload_messages[-1]["role"] == "tool"
    assert edit_payload_messages[-1]["tool_call_id"] == "call_edit"
    assert "replacement(s)" in edit_payload_messages[-1]["content"]
