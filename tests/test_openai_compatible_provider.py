from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from sdk_test_support import (
    SSE_HEADERS,
    Recorder,
    async_sdk,
    error_response,
    json_response,
    resolved_config,
    sse_body,
    sync_sdk,
    text_chunk,
    tool_call_chunk,
    usage_chunk,
)

from infrastructure.config.env import (
    ResolvedProviderConfig,
    load_provider_config,
    provider_env_prefix,
)
from infrastructure.providers.catalog import BUILTIN_PROVIDERS, get_provider_definition
from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
)
from infrastructure.providers.connection import ProviderConnectionService
from infrastructure.providers.http import provider_error_from_http_status
from infrastructure.providers.model_catalog import (
    ModelCatalogClient,
    fetch_models_for_connect,
)
from infrastructure.providers.model_catalog import (
    test_model_connection as probe_model_connection,
)
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.tools.types import ToolCall


def sdk_collect(
    config: ResolvedProviderConfig,
    chunks: list[dict[str, Any]],
    snapshot: ContextSnapshot,
) -> tuple[list[ModelStreamEvent], Recorder]:
    recorder = Recorder(
        lambda _request: httpx.Response(
            200,
            content=sse_body(chunks),
            headers=SSE_HEADERS,
        )
    )
    sdk = async_sdk(config, recorder)
    client = OpenAICompatibleChatCompletionsClient(config, sdk_client=sdk)

    async def run() -> list[ModelStreamEvent]:
        events = [event async for event in client.stream(snapshot)]
        await sdk.close()
        return events

    return asyncio.run(run()), recorder


def completed_event(events: list[ModelStreamEvent]) -> ModelStreamEvent:
    return next(
        event for event in reversed(events) if event.type == "message_completed"
    )


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


def test_catalog_contains_builtin_providers() -> None:
    expected = {
        "openai",
        "deepseek",
        "glm",
        "minimax",
        "siliconflow",
        "gemini",
        "custom",
    }

    assert expected <= set(BUILTIN_PROVIDERS)
    for provider_id in expected:
        assert BUILTIN_PROVIDERS[provider_id].id == provider_id
        if provider_id != "custom":
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
    config = resolved_config(default_params={"temperature": 0})
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

    _events, recorder = sdk_collect(
        config,
        [text_chunk("ok", finish_reason="stop")],
        snapshot,
    )

    request = recorder.requests[0]
    payload = recorder.body
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert payload["model"] == "gpt-test"
    assert payload["temperature"] == 0
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hello"},
    ]
    assert payload["tools"] == list(snapshot.tool_schemas)


def test_chat_completions_projects_internal_tool_results() -> None:
    config = resolved_config()
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

    _events, recorder = sdk_collect(
        config,
        [text_chunk("ok", finish_reason="stop")],
        snapshot,
    )

    assert recorder.body["messages"] == [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": "", "tool_calls": [assistant_tool_call]},
        {"role": "tool", "tool_call_id": "call_x", "content": "1\tcontents"},
    ]


def test_chat_completions_keeps_synthetic_attachment_context_user_side() -> None:
    config = resolved_config()
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

    _events, recorder = sdk_collect(
        config,
        [text_chunk("ok", finish_reason="stop")],
        snapshot,
    )

    message = recorder.body["messages"][0]
    assert message["role"] == "user"
    assert "tool_calls" not in message


def test_chat_completions_omits_empty_tools() -> None:
    _events, recorder = sdk_collect(
        resolved_config(),
        [text_chunk("ok", finish_reason="stop")],
        ContextSnapshot(system_prompt="", messages=()),
    )

    assert "tools" not in recorder.body


def test_chat_completions_applies_max_output_token_override() -> None:
    snapshot = ContextSnapshot(
        system_prompt="",
        messages=(),
        usage_hints={"request_overrides": {"max_output_tokens": 64000}},
    )

    _events, recorder = sdk_collect(
        resolved_config(default_params={"max_tokens": 8000}),
        [text_chunk("ok", finish_reason="stop")],
        snapshot,
    )

    assert recorder.body["max_tokens"] == 64000


def test_chat_completions_parses_text_response() -> None:
    events, _recorder = sdk_collect(
        resolved_config(),
        [
            text_chunk("hello", finish_reason="stop"),
            usage_chunk(prompt_tokens=10, completion_tokens=5, cached_tokens=3),
        ],
        ContextSnapshot(system_prompt="", messages=()),
    )

    response = completed_event(events)

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

    events, _recorder = sdk_collect(
        resolved_config(),
        [
            tool_call_chunk(
                0,
                call_id="call_x",
                name="read_file",
                arguments='{"path":"a.txt"}',
                finish_reason="tool_calls",
            )
        ],
        ContextSnapshot(system_prompt="", messages=()),
    )

    response = completed_event(events)

    assert response.final_text == ""
    assert response.metadata["tool_calls"] == (
        ToolCall(id="call_x", name="read_file", input={"path": "a.txt"}),
    )
    assert response.assistant_message["tool_calls"] == [raw_tool_call]


def test_chat_completions_generates_fallback_tool_call_id() -> None:
    events, _recorder = sdk_collect(
        resolved_config(),
        [
            tool_call_chunk(
                0, name="read_file", arguments="", finish_reason="tool_calls"
            )
        ],
        ContextSnapshot(system_prompt="", messages=()),
    )

    response = completed_event(events)

    assert response.metadata["tool_calls"] == (
        ToolCall(id="call_0", name="read_file", input={}),
    )


def test_list_models_parses_openai_compatible_response() -> None:
    recorder = Recorder(
        lambda _request: json_response(
            {
                "object": "list",
                "data": [
                    {
                        "id": "z-model",
                        "object": "model",
                        "created": 1,
                        "owned_by": "owner",
                    },
                    {
                        "id": "a-model",
                        "object": "model",
                        "created": 2,
                        "display_name": "A Model",
                    },
                ],
            }
        )
    )
    sdk = sync_sdk(base_url="https://api.openai.com/v1", handler=recorder)
    client = ModelCatalogClient(resolved_config(), sdk_client=sdk)

    models = client.list_models()
    sdk.close()

    assert [model.id for model in models] == ["a-model", "z-model"]
    assert models[0].display_name == "A Model"
    assert models[1].owned_by == "owner"
    assert str(recorder.requests[0].url) == "https://api.openai.com/v1/models"


def test_fetch_models_for_connect_uses_sdk_and_sorts_results() -> None:
    recorder = Recorder(
        lambda _request: json_response(
            {
                "object": "list",
                "data": [
                    {"id": "z-model", "object": "model", "created": 1},
                    {"id": "a-model", "object": "model", "created": 2},
                ],
            }
        )
    )
    provider = get_provider_definition("custom")
    http_client = httpx.Client(transport=httpx.MockTransport(recorder))

    models = fetch_models_for_connect(
        provider,
        "secret",
        "https://example.test/v1",
        http_client=http_client,
    )
    http_client.close()

    assert [model.id for model in models] == ["a-model", "z-model"]
    assert str(recorder.requests[0].url) == "https://example.test/v1/models"


def test_fetch_models_for_connect_falls_back_to_second_candidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/models"):
            return error_response(404, "not found")
        return json_response(
            {
                "object": "list",
                "data": [{"id": "fallback", "object": "model", "created": 1}],
            }
        )

    recorder = Recorder(handler)
    provider = get_provider_definition("custom")
    http_client = httpx.Client(transport=httpx.MockTransport(recorder))

    models = fetch_models_for_connect(
        provider,
        "secret",
        "https://example.test",
        http_client=http_client,
    )
    http_client.close()

    assert [model.id for model in models] == ["fallback"]
    assert [str(request.url) for request in recorder.requests] == [
        "https://example.test/v1/models",
        "https://example.test/models",
    ]


class _FakeSyncTransport:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.get_calls: list[str] = []
        self.post_calls: list[str] = []

    def get_json(
        self, url: str, headers: dict[str, str], timeout_seconds: float
    ) -> dict[str, Any]:
        self.get_calls.append(url)
        return self.response

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.post_calls.append(url)
        return {"ok": True}


def test_fetch_models_for_connect_keeps_ollama_native_endpoint() -> None:
    transport = _FakeSyncTransport(
        {"models": [{"name": "llama3:latest", "model": "llama3:latest"}]}
    )
    provider = get_provider_definition("ollama")

    models = fetch_models_for_connect(provider, "", None, transport=transport)

    assert [model.id for model in models] == ["llama3:latest"]
    assert transport.get_calls == ["http://localhost:11434/api/tags"]


def test_model_connection_uses_sdk_chat_endpoint() -> None:
    recorder = Recorder(
        lambda _request: json_response(
            {
                "id": "1",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hi"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
    )
    provider = get_provider_definition("openai")
    http_client = httpx.Client(transport=httpx.MockTransport(recorder))

    error = probe_model_connection(
        provider,
        "secret",
        "gpt-test",
        "https://api.openai.com/v1",
        http_client=http_client,
    )
    http_client.close()

    assert error is None
    assert str(recorder.requests[0].url) == "https://api.openai.com/v1/chat/completions"


def test_model_connection_returns_readable_error() -> None:
    provider = get_provider_definition("openai")
    recorder = Recorder(lambda _request: error_response(401, "bad key"))
    http_client = httpx.Client(transport=httpx.MockTransport(recorder))

    error = probe_model_connection(
        provider,
        "secret",
        "gpt-test",
        "https://api.openai.com/v1",
        http_client=http_client,
    )
    http_client.close()

    assert error == "bad key"


def test_model_connection_keeps_ollama_native_chat_endpoint() -> None:
    transport = _FakeSyncTransport({})
    provider = get_provider_definition("ollama")

    error = probe_model_connection(
        provider, "", "llama3:latest", None, transport=transport
    )

    assert error is None
    assert transport.post_calls == ["http://localhost:11434/api/chat"]


def test_connect_options_are_derived_from_catalog() -> None:
    service = ProviderConnectionService()

    options = service.list_connect_options()

    provider_ids = {option.provider_id for option in options}
    assert {"openai", "deepseek", "custom"} <= provider_ids
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
