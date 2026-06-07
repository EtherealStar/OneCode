from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.catalog import get_provider_definition
from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
)
from services.context.snapshot import ContextSnapshot
from services.model.types import ProviderError
from services.tools.types import ToolCall


def resolved_config() -> ResolvedProviderConfig:
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


@dataclass
class FakeAsyncTransport:
    chunks: list[dict[str, Any] | BaseException]
    calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = field(
        default_factory=list
    )

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        raise AssertionError("streaming tests should not call post_json")

    async def stream_json_lines(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any]]:
        self.calls.append((url, headers, payload, timeout_seconds))
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def test_chat_completions_streams_text_deltas_and_final_message() -> None:
    transport = FakeAsyncTransport(
        [
            {"choices": [{"delta": {"content": "hello"}}]},
            {"choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 2, "completion_tokens": 3}},
        ]
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    async def run() -> list:
        return [event async for event in client.stream(ContextSnapshot("", ()))]

    events = asyncio.run(run())

    assert [event.text for event in events if event.type == "content_delta"] == [
        "hello",
        " world",
    ]
    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.final_text == "hello world"
    assert completed.stop_reason == "stop"
    assert completed.usage is not None
    assert completed.usage.input_tokens == 2
    assert transport.calls[0][2]["stream"] is True


def test_chat_completions_marks_length_finish_as_output_interrupted() -> None:
    transport = FakeAsyncTransport(
        [
            {"choices": [{"delta": {"content": "cut"}, "finish_reason": "length"}]},
        ]
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    async def run() -> list:
        return [event async for event in client.stream(ContextSnapshot("", ()))]

    events = asyncio.run(run())

    completed = events[-1]
    assert completed.type == "message_completed"
    assert completed.stop_reason == "length"
    assert completed.output_interrupted is True


def test_chat_completions_stream_accumulates_tool_call_arguments() -> None:
    transport = FakeAsyncTransport(
        [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_x",
                                    "function": {
                                        "name": "read_",
                                        "arguments": '{"file_',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "name": "file",
                                        "arguments": 'path":"a.txt"}',
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        ]
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    async def run() -> list:
        return [event async for event in client.stream(ContextSnapshot("", ()))]

    events = asyncio.run(run())
    tool_completed = next(
        event for event in events if event.type == "tool_call_completed"
    )

    assert tool_completed.tool_call == ToolCall(
        id="call_x",
        name="read_file",
        input={"file_path": "a.txt"},
    )
    assert events[-1].metadata["tool_calls"] == (tool_completed.tool_call,)


def test_chat_completions_stream_rethrows_provider_errors() -> None:
    transport = FakeAsyncTransport(
        [
            ProviderError(
                "bad stream",
                provider_id="openai",
                error_type="invalid_response",
            )
        ]
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    async def run() -> None:
        async for _event in client.stream(ContextSnapshot("", ())):
            pass

    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(run())

    assert exc_info.value.error_type == "invalid_response"
