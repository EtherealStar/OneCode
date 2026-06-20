from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from infrastructure.config.env import ResolvedProviderConfig, load_provider_config
from infrastructure.config.env import provider_env_prefix
from infrastructure.providers.catalog import BUILTIN_PROVIDERS, get_provider_definition
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.connection import ProviderConnectionService
from infrastructure.providers.http import provider_error_from_http_status
from infrastructure.providers.model_catalog import ModelCatalogClient
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.tools.types import ToolCall


@dataclass
class FakeTransport:
    post_response: dict[str, Any] | None = None
    get_response: dict[str, Any] | None = None
    post_calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = field(
        default_factory=list
    )
    get_calls: list[tuple[str, dict[str, str], float]] = field(default_factory=list)

    async def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.post_calls.append((url, headers, payload, timeout_seconds))
        assert self.post_response is not None
        return self.post_response

    async def stream_json_lines(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> AsyncIterator[dict[str, Any]]:
        response = await self.post_json(url, headers, payload, timeout_seconds)
        message = response["choices"][0]["message"]
        finish_reason = response["choices"][0].get("finish_reason")
        delta: dict[str, Any] = {}
        content = message.get("content")
        if content is not None:
            delta["content"] = _content_to_text(content)
        if message.get("tool_calls") is not None:
            delta["tool_calls"] = [
                {"index": index, **tool_call}
                for index, tool_call in enumerate(message["tool_calls"])
            ]
            finish_reason = finish_reason or "tool_calls"
        yield {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
        if response.get("usage") is not None:
            yield {"usage": response["usage"]}

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.get_calls.append((url, headers, timeout_seconds))
        assert self.get_response is not None
        return self.get_response


def collect_stream(
    client: OpenAICompatibleChatCompletionsClient,
    snapshot: ContextSnapshot,
) -> list[ModelStreamEvent]:
    async def run() -> list[ModelStreamEvent]:
        return [event async for event in client.stream(snapshot)]

    return asyncio.run(run())


def completed_event(events: list[ModelStreamEvent]) -> ModelStreamEvent:
    return next(event for event in reversed(events) if event.type == "message_completed")


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def write_env(
    tmp_path: Path,
    *,
    provider_id: str = "openai",
    model: str = "gpt-test",
    base_url: str | None = None,
    api_key: str = "secret",
    timeout_seconds: float | None = None,
    extra_headers: str | None = None,
    default_params: str | None = None,
) -> Path:
    prefix = provider_env_prefix(provider_id)
    lines = [
        f"ONECODE_PROVIDER_ID={provider_id}",
        f"#{provider_id}",
        f"{prefix}_MODEL={model}",
        f"{prefix}_API_KEY={api_key}",
    ]
    if base_url is not None:
        lines.append(f"{prefix}_BASE_URL={base_url}")
    if timeout_seconds is not None:
        lines.append(f"ONECODE_TIMEOUT_SECONDS={timeout_seconds}")
    if extra_headers is not None:
        lines.append(f"ONECODE_EXTRA_HEADERS={extra_headers}")
    if default_params is not None:
        lines.append(f"ONECODE_DEFAULT_PARAMS={default_params}")
    env_path = tmp_path / ".env"
    env_path.write_text("\n".join(lines), encoding="utf-8")
    return env_path


def resolved_config(
    *,
    provider_id: str = "openai",
    model: str = "gpt-test",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "secret",
    default_params: dict[str, Any] | None = None,
) -> ResolvedProviderConfig:
    provider = get_provider_definition(provider_id)
    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        base_url,
        model,
        api_key,
        default_params=default_params or {},
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


def test_catalog_contains_builtin_providers() -> None:
    expected = {
        "openai",
        "deepseek",
        "glm",
        "minimax",
        "siliconflow",
        "gemini",
        "claude-openai-compatible",
        "custom",
    }

    assert expected <= set(BUILTIN_PROVIDERS)
    for provider_id in expected:
        assert BUILTIN_PROVIDERS[provider_id].id == provider_id
        if provider_id not in {"custom", "claude-openai-compatible"}:
            assert BUILTIN_PROVIDERS[provider_id].base_url


def test_load_provider_config_from_dotenv_file(tmp_path: Path) -> None:
    env_path = write_env(
        tmp_path,
        base_url="https://example.test/v1/",
        timeout_seconds=12,
        extra_headers='{"X-Test":"yes"}',
        default_params='{"temperature":0}',
    )

    config = load_provider_config(env_path)

    assert config.provider_id == "openai"
    assert config.model == "gpt-test"
    assert config.base_url == "https://example.test/v1"
    assert config.api_key == "secret"
    assert config.timeout_seconds == 12.0
    assert config.headers == {"X-Test": "yes"}
    assert config.default_params == {"temperature": 0}


def test_load_provider_config_requires_dotenv_file(tmp_path: Path) -> None:
    with pytest.raises(ProviderError) as exc_info:
        load_provider_config(tmp_path / ".env")

    assert exc_info.value.error_type == "configuration_error"


def test_load_provider_config_requires_api_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ONECODE_PROVIDER_ID=openai\n#openai\nOPENAI_MODEL=gpt-test\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderError) as exc_info:
        load_provider_config(env_path)

    assert exc_info.value.error_type == "configuration_error"
    assert "OPENAI_API_KEY" in str(exc_info.value)


def test_load_provider_config_requires_custom_base_url(tmp_path: Path) -> None:
    env_path = write_env(tmp_path, provider_id="custom")

    with pytest.raises(ProviderError) as exc_info:
        load_provider_config(env_path)

    assert exc_info.value.error_type == "configuration_error"


def test_load_provider_config_rejects_invalid_json_object(tmp_path: Path) -> None:
    env_path = write_env(tmp_path, default_params="[]")

    with pytest.raises(ProviderError) as exc_info:
        load_provider_config(env_path)

    assert exc_info.value.error_type == "configuration_error"


def test_resolved_provider_config_repr_hides_api_key(tmp_path: Path) -> None:
    config = load_provider_config(write_env(tmp_path, api_key="super-secret"))

    assert "super-secret" not in repr(config)


def test_dotenv_interpolation_is_disabled(tmp_path: Path) -> None:
    config = load_provider_config(write_env(tmp_path, api_key="${OPENAI_API_KEY}"))

    assert config.api_key == "${OPENAI_API_KEY}"


def test_chat_completions_payload_includes_messages_and_tools() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(default_params={"temperature": 0}),
        async_transport=transport,
    )
    snapshot = ContextSnapshot(
        system_prompt="system",
        messages=({"role": "user", "content": "hello"},),
        tool_schemas=(
            {
                "type": "function",
                "function": {"name": "read_file", "parameters": {"type": "object"}},
            },
        ),
    )

    collect_stream(client, snapshot)

    url, headers, payload, timeout = transport.post_calls[0]
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer " + "secret"
    assert timeout == 60.0
    assert payload["model"] == "gpt-test"
    assert payload["temperature"] == 0
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    assert payload["tools"] == list(snapshot.tool_schemas)


def test_chat_completions_projects_internal_tool_results() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )
    assistant_tool_call = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
    }
    snapshot = ContextSnapshot(
        system_prompt="",
        messages=(
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "", "tool_calls": [assistant_tool_call]},
            {
                "role": "tool_result",
                "tool_call_id": "call_x",
                "tool_name": "read_file",
                "content": "1\tcontents",
                "is_error": False,
                "metadata": {},
            },
        ),
    )

    collect_stream(client, snapshot)

    payload = transport.post_calls[0][2]
    assert payload["messages"] == [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": "", "tool_calls": [assistant_tool_call]},
        {"role": "tool", "tool_call_id": "call_x", "content": "1\tcontents"},
    ]


def test_chat_completions_keeps_synthetic_attachment_context_user_side() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )
    snapshot = ContextSnapshot(
        system_prompt="",
        messages=(
            {
                "role": "user",
                "content": "[attachment file]\nEquivalent tool: read_file\nResult:\n1\tcontents",
                "metadata": {
                    "synthetic": True,
                    "source": "attachment",
                    "attachment_type": "file",
                },
            },
        ),
    )

    collect_stream(client, snapshot)

    payload = transport.post_calls[0][2]
    assert payload["messages"][0]["role"] == "user"
    assert "tool_calls" not in payload["messages"][0]


def test_chat_completions_omits_empty_tools() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    collect_stream(client, ContextSnapshot(system_prompt="", messages=()))

    assert "tools" not in transport.post_calls[0][2]


def test_chat_completions_applies_max_output_token_override() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(default_params={"max_tokens": 8000}),
        async_transport=transport,
    )
    snapshot = ContextSnapshot(
        system_prompt="",
        messages=(),
        usage_hints={"request_overrides": {"max_output_tokens": 64000}},
    )

    collect_stream(client, snapshot)

    assert transport.post_calls[0][2]["max_tokens"] == 64000


def test_chat_completions_parses_text_response() -> None:
    transport = FakeTransport(
        post_response={
            "choices": [
                {
                    "message": {"content": [{"type": "text", "text": "hello"}]},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    response = completed_event(
        collect_stream(client, ContextSnapshot(system_prompt="", messages=()))
    )

    assert response.final_text == "hello"
    assert response.stop_reason == "stop"
    assert response.usage is not None
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.usage.cache_read_input_tokens == 3


def test_chat_completions_parses_tool_calls() -> None:
    raw_tool_call = {
        "id": "call_x",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
    }
    transport = FakeTransport(
        post_response={
            "choices": [{"message": {"content": None, "tool_calls": [raw_tool_call]}}]
        },
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    response = completed_event(
        collect_stream(client, ContextSnapshot(system_prompt="", messages=()))
    )

    assert response.final_text == ""
    assert response.metadata["tool_calls"] == (
        ToolCall(id="call_x", name="read_file", input={"path": "a.txt"}),
    )
    assert response.assistant_message["tool_calls"] == [raw_tool_call]


def test_chat_completions_generates_fallback_tool_call_id() -> None:
    transport = FakeTransport(
        post_response={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {"name": "read_file", "arguments": ""},
                            }
                        ]
                    }
                }
            ]
        },
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    response = completed_event(
        collect_stream(client, ContextSnapshot(system_prompt="", messages=()))
    )

    assert response.metadata["tool_calls"] == (
        ToolCall(id="call_0", name="read_file", input={}),
    )


def test_chat_completions_rejects_invalid_tool_arguments() -> None:
    transport = FakeTransport(
        post_response={
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_x",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "[]"},
                            }
                        ]
                    }
                }
            ]
        },
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        async_transport=transport,
    )

    with pytest.raises(ProviderError) as exc_info:
        collect_stream(client, ContextSnapshot(system_prompt="", messages=()))

    assert exc_info.value.error_type == "invalid_tool_arguments"


def test_list_models_parses_openai_compatible_response() -> None:
    transport = FakeTransport(
        get_response={
            "data": [
                {"id": "z-model", "owned_by": "owner"},
                {"id": "a-model", "display_name": "A Model"},
            ]
        }
    )
    client = ModelCatalogClient(resolved_config(), transport=transport)

    models = client.list_models()

    assert [model.id for model in models] == ["a-model", "z-model"]
    assert models[0].display_name == "A Model"
    assert models[1].owned_by == "owner"
    assert transport.get_calls[0][0] == "https://api.openai.com/v1/models"


def test_connect_options_are_derived_from_catalog() -> None:
    service = ProviderConnectionService()

    options = service.list_connect_options()

    assert [option.provider_id for option in options] == list(BUILTIN_PROVIDERS)
    custom = next(option for option in options if option.provider_id == "custom")
    assert custom.requires_base_url is True


def test_http_errors_are_provider_errors() -> None:
    auth = provider_error_from_http_status(
        401,
        '{"error":{"message":"bad key"}}',
        provider_id="openai",
    )
    rate_limit = provider_error_from_http_status(429, provider_id="openai")
    server = provider_error_from_http_status(500, provider_id="openai")

    assert auth.error_type == "authentication_error"
    assert auth.retryable is False
    assert str(auth) == "bad key"
    assert rate_limit.error_type == "rate_limit_error"
    assert rate_limit.retryable is True
    assert server.error_type == "server_error"
    assert server.retryable is True


def test_context_limit_http_errors_are_provider_neutral() -> None:
    payload = '{"error":{"message":"This model has too many tokens in the prompt"}}'

    too_large = provider_error_from_http_status(413, payload, provider_id="openai")
    bad_request = provider_error_from_http_status(400, payload, provider_id="openai")

    assert too_large.error_type == "context_limit_exceeded"
    assert too_large.retryable is False
    assert bad_request.error_type == "context_limit_exceeded"
    assert bad_request.retryable is False
