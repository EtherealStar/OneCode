from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.catalog import get_provider_definition
from services.context.message_store import MessageStore
from services.guard import SandboxBoundary, SandboxGuard
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from tools.edit_file import descriptor as edit_file_descriptor
from tools.read_file import descriptor as read_file_descriptor


@dataclass
class SequencedTransport:
    responses: list[dict[str, Any]]
    post_calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = field(
        default_factory=list
    )

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.post_calls.append((url, headers, payload, timeout_seconds))
        if not self.responses:
            raise AssertionError("Unexpected provider call")
        return self.responses.pop(0)


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
    transport: SequencedTransport,
) -> tuple[AgentLoop, ToolRegistry]:
    message_store = MessageStore()
    registry = ToolRegistry([read_file_descriptor(), edit_file_descriptor()])
    context_engine = ContextEngine(message_store, tool_schema_provider=registry)
    guard = SandboxGuard(SandboxBoundary(cwd=workspace))
    loop = AgentLoop(
        state=RuntimeState(),
        message_store=message_store,
        context_engine=context_engine,
        model_client=OpenAICompatibleChatCompletionsClient(
            make_config(),
            transport=transport,
        ),
        tool_executor=RegistryToolExecutor(registry, guard=guard),
    )
    return loop, registry


def tool_call_response(
    call_id: str,
    name: str,
    arguments: str,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": arguments,
                            },
                        }
                    ],
                }
            }
        ]
    }


def final_response(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def test_provider_loop_can_read_file_with_registry_executor(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    transport = SequencedTransport(
        [
            tool_call_response(
                "call_read",
                "read_file",
                '{"file_path":"a.txt"}',
            ),
            final_response("read complete"),
        ]
    )
    loop, registry = make_loop(workspace, transport)

    result = loop.run("inspect a.txt")

    assert result == "read complete"
    assert len(transport.post_calls) == 2
    first_payload = transport.post_calls[0][2]
    assert first_payload["tools"] == list(registry.tool_schemas(loop.state))
    second_messages = transport.post_calls[1][2]["messages"]
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
    transport = SequencedTransport(
        [
            tool_call_response(
                "call_read",
                "read_file",
                '{"file_path":"a.txt"}',
            ),
            tool_call_response(
                "call_edit",
                "edit_file",
                (
                    '{"file_path":"a.txt","old_string":"old",'
                    '"new_string":"new"}'
                ),
            ),
            final_response("edit complete"),
        ]
    )
    loop, _registry = make_loop(workspace, transport)

    result = loop.run("change a.txt")

    assert result == "edit complete"
    assert target.read_text(encoding="utf-8") == "hello new world"
    assert len(transport.post_calls) == 3
    edit_payload_messages = transport.post_calls[2][2]["messages"]
    assert edit_payload_messages[-1]["role"] == "tool"
    assert edit_payload_messages[-1]["tool_call_id"] == "call_edit"
    assert "replacement(s)" in edit_payload_messages[-1]["content"]
