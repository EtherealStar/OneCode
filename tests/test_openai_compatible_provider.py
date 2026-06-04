from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from core.context_engine import ContextEngine
from core.loop import AgentLoop
from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig, load_provider_config
from infrastructure.providers.catalog import BUILTIN_PROVIDERS, get_provider_definition
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.connection import ProviderConnectionService
from infrastructure.providers.factory import create_model_client
from infrastructure.providers.http import provider_error_from_http_status
from infrastructure.providers.model_catalog import ModelCatalogClient
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot
from services.model.types import ProviderError
from services.tools.types import ToolCall, ToolExecutionResult


@dataclass
class FakeTransport:
    post_response: dict[str, Any] | None = None
    get_response: dict[str, Any] | None = None
    post_calls: list[tuple[str, dict[str, str], dict[str, Any], float]] = field(
        default_factory=list
    )
    get_calls: list[tuple[str, dict[str, str], float]] = field(default_factory=list)

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.post_calls.append((url, headers, payload, timeout_seconds))
        assert self.post_response is not None
        return self.post_response

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        self.get_calls.append((url, headers, timeout_seconds))
        assert self.get_response is not None
        return self.get_response


@dataclass
class FakeToolExecutor:
    calls: list[tuple[ToolCall, ...]] = field(default_factory=list)

    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ) -> list[ToolExecutionResult]:
        self.calls.append(tool_calls)
        return [
            ToolExecutionResult(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                content=f"result for {tool_call.name}",
            )
            for tool_call in tool_calls
        ]


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
    lines = [
        f"ONECODE_PROVIDER_ID={provider_id}",
        f"ONECODE_MODEL={model}",
        f"ONECODE_API_KEY={api_key}",
    ]
    if base_url is not None:
        lines.append(f"ONECODE_BASE_URL={base_url}")
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
        "ONECODE_PROVIDER_ID=openai\nONECODE_MODEL=gpt-test\n",
        encoding="utf-8",
    )

    with pytest.raises(ProviderError) as exc_info:
        load_provider_config(env_path)

    assert exc_info.value.error_type == "configuration_error"
    assert "ONECODE_API_KEY" in str(exc_info.value)


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
        transport=transport,
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

    client.send(snapshot)

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
        transport=transport,
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

    client.send(snapshot)

    payload = transport.post_calls[0][2]
    assert payload["messages"] == [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": "", "tool_calls": [assistant_tool_call]},
        {"role": "tool", "tool_call_id": "call_x", "content": "1\tcontents"},
    ]


def test_chat_completions_omits_empty_tools() -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "ok"}}]},
    )
    client = OpenAICompatibleChatCompletionsClient(
        resolved_config(),
        transport=transport,
    )

    client.send(ContextSnapshot(system_prompt="", messages=()))

    assert "tools" not in transport.post_calls[0][2]


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
        transport=transport,
    )

    response = client.send(ContextSnapshot(system_prompt="", messages=()))

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
        transport=transport,
    )

    response = client.send(ContextSnapshot(system_prompt="", messages=()))

    assert response.final_text == ""
    assert response.tool_calls == (
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
        transport=transport,
    )

    response = client.send(ContextSnapshot(system_prompt="", messages=()))

    assert response.tool_calls == (ToolCall(id="call_0", name="read_file", input={}),)


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
        transport=transport,
    )

    with pytest.raises(ProviderError) as exc_info:
        client.send(ContextSnapshot(system_prompt="", messages=()))

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


def test_real_provider_client_can_be_injected_into_loop_for_final_text(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(
        post_response={"choices": [{"message": {"content": "done"}}]},
    )
    model_client = create_model_client(
        write_env(tmp_path),
        transport=transport,
    )
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,
        tool_executor=FakeToolExecutor(),
    )

    result = loop.run("hello")

    assert result == "done"
    assert transport.post_calls[0][2]["messages"] == [
        {"role": "user", "content": "hello"}
    ]


def test_real_provider_client_can_drive_tool_call_loop(tmp_path: Path) -> None:
    first_response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"a.txt"}',
                            },
                        }
                    ]
                }
            }
        ]
    }

    class SequencedTransport(FakeTransport):
        def post_json(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, Any],
            timeout_seconds: float,
        ) -> dict[str, Any]:
            self.post_calls.append((url, headers, payload, timeout_seconds))
            if len(self.post_calls) == 1:
                return first_response
            return {"choices": [{"message": {"content": "final"}}]}

    sequenced_transport = SequencedTransport()
    model_client = create_model_client(
        write_env(tmp_path),
        transport=sequenced_transport,
    )
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        flush_interval_seconds=60,
    )
    tool_executor = FakeToolExecutor()
    loop = AgentLoop(
        state=state,
        message_store=message_store,
        context_engine=ContextEngine(message_store),
        model_client=model_client,
        tool_executor=tool_executor,
    )

    result = loop.run("inspect")

    assert result == "final"
    assert len(sequenced_transport.post_calls) == 2
    assert tool_executor.calls == [
        (ToolCall(id="call_x", name="read_file", input={"path": "a.txt"}),)
    ]
    assert sequenced_transport.post_calls[1][2]["messages"] == [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_x",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"a.txt"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_x", "content": "result for read_file"},
    ]
