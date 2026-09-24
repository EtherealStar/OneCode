"""Milestone 1 contract tests for the locked OpenAI SDK and request projection.

Batch 1 proves the installed `openai==3.18.0` behavior against
`httpx.MockTransport` (base_url join, headers, auth, timeout, no hidden
retries, typed chunks, models list, typed errors). Batch 2 pins the
config-to-request projection used by the Chat Completions adapter.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any

import httpx
import openai
import pytest
from openai import AsyncOpenAI, OpenAI

from core.runtime_state import RuntimeState
from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.catalog import get_provider_definition
from infrastructure.providers.chat_completions import (
    SDK_CHAT_OPTION_NAMES,
    build_chat_completions_request,
)
from services.attachments.projector import AttachmentProjector
from services.attachments.types import AttachmentMessage
from services.context.snapshot import ContextSnapshot

SSE_HEADERS = {"content-type": "text/event-stream"}
JSON_HEADERS = {"content-type": "application/json"}


def resolved_config(
    *,
    provider_id: str = "openai",
    model: str = "gpt-test",
    base_url: str = "https://api.openai.com/v1",
    api_key: str = "secret",
    timeout_seconds: float = 60.0,
    headers: dict[str, str] | None = None,
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
        timeout_seconds=timeout_seconds,
        headers=headers or {},
        default_params=default_params or {},
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


def sse_text(chunks: list[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def chat_chunk(*, content: str | None = None, finish_reason: str | None = None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    return {
        "id": "chunk",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


class Recorder:
    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[0].content)


def async_sdk(config: ResolvedProviderConfig, handler: Callable[..., httpx.Response]) -> AsyncOpenAI:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AsyncOpenAI(
        api_key=config.api_key or "local-placeholder",
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
        default_headers=config.headers or None,
        http_client=client,
    )


def run_stream_chunks(config: ResolvedProviderConfig, chunks: list[dict[str, Any]]) -> list[Any]:
    recorder = Recorder(
        lambda _request: httpx.Response(200, content=sse_text(chunks), headers=SSE_HEADERS)
    )
    sdk = async_sdk(config, recorder)
    collected: list[Any] = []

    async def run() -> None:
        stream = await sdk.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        async for chunk in stream:
            collected.append(chunk)
        await sdk.close()

    asyncio.run(run())
    return collected


# --- Batch 1: locked SDK behavior -------------------------------------------


def test_sdk_client_mirrors_provider_transport_settings() -> None:
    config = resolved_config(
        base_url="https://example.test/v1",
        api_key="secret",
        timeout_seconds=60.0,
        headers={"X-Test": "yes"},
    )
    recorder = Recorder(
        lambda _request: httpx.Response(
            200,
            content=sse_text([chat_chunk(content="ok", finish_reason="stop")]),
            headers=SSE_HEADERS,
        )
    )
    sdk = async_sdk(config, recorder)

    assert sdk.timeout == 60.0
    assert sdk.max_retries == 0
    assert str(sdk.base_url).startswith("https://example.test/v1")

    async def run() -> None:
        stream = await sdk.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        async for _chunk in stream:
            pass
        await sdk.close()

    asyncio.run(run())

    request = recorder.requests[0]
    assert str(request.url) == "https://example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["x-test"] == "yes"
    assert request.headers["content-type"] == "application/json"


def test_sdk_typed_stream_exposes_text_tool_calls_usage_and_finish() -> None:
    chunks = [
        chat_chunk(content="hello"),
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "read_", "arguments": '{"p":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"name": "file", "arguments": '"a"}'},
                            }
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
        {
            "id": "chunk",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-test",
            "choices": [],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
                "prompt_tokens_details": {"cached_tokens": 1},
            },
        },
    ]

    collected = run_stream_chunks(resolved_config(), chunks)

    assert collected[0].choices[0].delta.content == "hello"
    first_tool = collected[1].choices[0].delta.tool_calls[0]
    assert first_tool.index == 0
    assert first_tool.id == "call_1"
    assert first_tool.function.name == "read_"
    assert first_tool.function.arguments == '{"p":'
    second_tool = collected[2].choices[0].delta.tool_calls[0]
    assert second_tool.id is None
    assert second_tool.function.name == "file"
    assert collected[2].choices[0].finish_reason == "tool_calls"
    assert collected[3].usage.prompt_tokens == 4
    assert collected[3].usage.prompt_tokens_details.cached_tokens == 1


def test_sdk_forwards_named_params_and_extra_body() -> None:
    config = resolved_config()
    recorder = Recorder(
        lambda _request: httpx.Response(200, content="{}", headers=JSON_HEADERS)
    )
    sdk = async_sdk(config, recorder)

    async def run() -> None:
        await sdk.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
            temperature=0.2,
            stream_options={"include_usage": True},
            max_tokens=128,
            extra_body={"custom_flag": True},
        )
        await sdk.close()

    asyncio.run(run())

    body = recorder.body
    assert body["temperature"] == 0.2
    assert body["stream_options"] == {"include_usage": True}
    assert body["max_tokens"] == 128
    assert body["custom_flag"] is True


def test_sdk_chat_option_names_track_installed_create_signature() -> None:
    sdk = AsyncOpenAI(api_key="secret", base_url="https://example.test/v1")
    parameters = inspect.signature(sdk.chat.completions.create).parameters
    signature_names = set(parameters)

    assert SDK_CHAT_OPTION_NAMES <= signature_names
    assert {"model", "messages", "stream", "tools"} <= signature_names
    assert SDK_CHAT_OPTION_NAMES.isdisjoint(
        {"extra_headers", "extra_query", "extra_body", "timeout"}
    )

    asyncio.run(sdk.close())


def test_sdk_models_list_returns_typed_models_async_and_sync() -> None:
    payload = json.dumps(
        {
            "object": "list",
            "data": [
                {"id": "gpt-a", "object": "model", "created": 1, "owned_by": "owner"},
                {"id": "gpt-b", "object": "model", "created": 2, "owned_by": "owner"},
            ],
        }
    )
    async_recorder = Recorder(
        lambda _request: httpx.Response(200, content=payload, headers=JSON_HEADERS)
    )
    async_sdk_client = async_sdk(resolved_config(), async_recorder)

    async def run() -> list[str]:
        page = await async_sdk_client.models.list()
        await async_sdk_client.close()
        return [model.id for model in page.data]

    assert asyncio.run(run()) == ["gpt-a", "gpt-b"]
    assert str(async_recorder.requests[0].url) == "https://api.openai.com/v1/models"

    sync_recorder = Recorder(
        lambda _request: httpx.Response(200, content=payload, headers=JSON_HEADERS)
    )
    sync_client = OpenAI(
        api_key="secret",
        base_url="https://api.openai.com/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(sync_recorder)),
    )
    page = sync_client.models.list()
    sync_client.close()

    assert [model.id for model in page.data] == ["gpt-a", "gpt-b"]


def test_sdk_max_retries_zero_sends_single_request_on_retryable_status() -> None:
    recorder = Recorder(
        lambda _request: httpx.Response(
            500,
            content=json.dumps({"error": {"message": "server exploded"}}),
            headers=JSON_HEADERS,
        )
    )
    sdk = async_sdk(resolved_config(), recorder)

    async def run() -> None:
        await sdk.chat.completions.create(
            model="gpt-test",
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )

    with pytest.raises(openai.InternalServerError) as exc_info:
        asyncio.run(run())
    asyncio.run(sdk.close())

    assert len(recorder.requests) == 1
    assert exc_info.value.status_code == 500


def test_sdk_stream_close_is_safe_after_partial_consumption() -> None:
    config = resolved_config()
    recorder = Recorder(
        lambda _request: httpx.Response(
            200,
            content=sse_text(
                [chat_chunk(content="a"), chat_chunk(content="b", finish_reason="stop")]
            ),
            headers=SSE_HEADERS,
        )
    )
    sdk = async_sdk(config, recorder)

    async def run() -> None:
        stream = await sdk.chat.completions.create(
            model=config.model,
            messages=[{"role": "user", "content": "hi"}],
            stream=True,
        )
        first = await stream.__anext__()
        assert first.choices[0].delta.content == "a"
        await stream.close()
        await stream.close()
        await sdk.close()
        assert sdk.is_closed() is True
        await sdk.close()

    asyncio.run(run())


def test_sdk_usage_chunk_may_be_absent_without_failing() -> None:
    collected = run_stream_chunks(
        resolved_config(),
        [chat_chunk(content="ok", finish_reason="stop")],
    )

    assert collected[-1].choices[0].finish_reason == "stop"
    assert all(getattr(chunk, "usage", None) is None for chunk in collected)


def test_sdk_status_and_connection_errors_are_typed() -> None:
    status_cases = {
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        429: openai.RateLimitError,
        500: openai.InternalServerError,
        413: openai.APIStatusError,
        400: openai.BadRequestError,
    }

    async def call_status(status: int, headers: dict[str, str] | None = None) -> BaseException:
        recorder = Recorder(
            lambda _request: httpx.Response(
                status,
                content=json.dumps({"error": {"message": "err"}}),
                headers={**(headers or {}), **JSON_HEADERS},
            )
        )
        sdk = async_sdk(resolved_config(), recorder)
        try:
            await sdk.chat.completions.create(
                model="gpt-test",
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )
        except BaseException as exc:  # noqa: BLE001 - the test records the boundary type
            await sdk.close()
            return exc
        raise AssertionError("expected an SDK error")

    for status, expected in status_cases.items():
        error = asyncio.run(call_status(status))
        assert isinstance(error, expected)
        assert error.status_code == status

    rate_limit = asyncio.run(call_status(429, {"retry-after": "7"}))
    assert rate_limit.response.headers["retry-after"] == "7"

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    connect_sdk = async_sdk(resolved_config(), fail)

    async def call_connection() -> BaseException:
        try:
            await connect_sdk.chat.completions.create(
                model="gpt-test",
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )
        except BaseException as exc:  # noqa: BLE001
            await connect_sdk.close()
            return exc
        raise AssertionError("expected a connection error")

    assert isinstance(asyncio.run(call_connection()), openai.APIConnectionError)

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    timeout_sdk = async_sdk(resolved_config(), timeout)

    async def call_timeout() -> BaseException:
        try:
            await timeout_sdk.chat.completions.create(
                model="gpt-test",
                messages=[{"role": "user", "content": "hi"}],
                stream=False,
            )
        except BaseException as exc:  # noqa: BLE001
            await timeout_sdk.close()
            return exc
        raise AssertionError("expected a timeout error")

    assert isinstance(asyncio.run(call_timeout()), openai.APITimeoutError)


# --- Batch 2: adapter request projection ------------------------------------


def send_built_request(
    request: Any,
    config: ResolvedProviderConfig,
) -> dict[str, Any]:
    recorder = Recorder(
        lambda _req: httpx.Response(
            200,
            content=json.dumps(
                {
                    "id": "1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
            headers=JSON_HEADERS,
        )
    )
    sdk = async_sdk(config, recorder)

    async def run() -> None:
        await sdk.chat.completions.create(
            **request.named,
            extra_body=request.extra_body or None,
        )
        await sdk.close()

    asyncio.run(run())
    return recorder.body


def test_request_builder_classifies_named_params_and_extra_body() -> None:
    config = resolved_config(
        default_params={"temperature": 0.3, "provider_flag": "x"},
    )
    snapshot = ContextSnapshot("sys", ({"role": "user", "content": "hi"},))

    request = build_chat_completions_request(config, snapshot)

    assert request.named["temperature"] == 0.3
    assert "provider_flag" not in request.named
    assert request.extra_body == {"provider_flag": "x"}

    body = send_built_request(request, config)
    assert body["temperature"] == 0.3
    assert body["provider_flag"] == "x"


def test_request_builder_prevents_config_from_overriding_reserved_fields() -> None:
    config = resolved_config(
        default_params={
            "model": "evil-model",
            "messages": [{"role": "user", "content": "evil"}],
            "tools": [],
            "stream": False,
        },
    )
    tool_schema = {
        "type": "function",
        "function": {"name": "read_file", "parameters": {"type": "object"}},
    }
    snapshot = ContextSnapshot(
        "sys",
        ({"role": "user", "content": "hi"},),
        tool_schemas=(tool_schema,),
    )

    request = build_chat_completions_request(config, snapshot)

    assert request.named["model"] == "gpt-test"
    assert request.named["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert request.named["tools"] == [tool_schema]
    assert request.named["stream"] is True


def test_request_builder_output_limit_override_wins_over_config() -> None:
    config = resolved_config(default_params={"max_tokens": 8000})

    overridden = build_chat_completions_request(
        config,
        ContextSnapshot(
            "",
            (),
            usage_hints={"request_overrides": {"max_output_tokens": 64000}},
        ),
    )
    defaulted = build_chat_completions_request(
        config,
        ContextSnapshot("", ()),
    )

    assert overridden.named["max_tokens"] == 64000
    assert defaulted.named["max_tokens"] == 8000


def test_request_builder_stream_options_only_when_configured() -> None:
    snapshot = ContextSnapshot("", ())

    without = build_chat_completions_request(resolved_config(), snapshot)
    with_usage = build_chat_completions_request(
        resolved_config(default_params={"stream_options": {"include_usage": True}}),
        snapshot,
    )

    assert "stream_options" not in without.named
    assert with_usage.named["stream_options"] == {"include_usage": True}


def test_request_builder_hides_raw_attachment_role() -> None:
    attachment = AttachmentMessage(
        attachment={"type": "file", "path": "note.txt", "content": "1\tone"},
        attachment_id="att_1",
        source="user_input",
    ).to_message()
    projected = AttachmentProjector().project((attachment,), RuntimeState())
    config = resolved_config()

    request = build_chat_completions_request(
        config,
        ContextSnapshot("", projected),
    )

    messages = request.named["messages"]
    assert [message["role"] for message in messages] == ["user"]
    assert all(message.get("role") != "attachment" for message in messages)
    assert all("attachment" not in message for message in messages)

    body = send_built_request(request, config)
    assert all(message.get("role") != "attachment" for message in body["messages"])


def test_request_builder_leaves_authentication_to_sdk_api_key() -> None:
    config = resolved_config(api_key="secret", headers={"X-Test": "yes"})
    snapshot = ContextSnapshot("", ({"role": "user", "content": "hi"},))

    request = build_chat_completions_request(config, snapshot)

    assert "Authorization" not in request.named
    assert "Authorization" not in request.extra_body

    recorder = Recorder(
        lambda _req: httpx.Response(
            200,
            content=json.dumps(
                {
                    "id": "1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ),
            headers=JSON_HEADERS,
        )
    )
    sdk = async_sdk(config, recorder)

    async def run() -> None:
        await sdk.chat.completions.create(
            **request.named,
            extra_body=request.extra_body or None,
        )
        await sdk.close()

    asyncio.run(run())
    outbound = recorder.requests[0]
    assert outbound.headers["authorization"] == "Bearer secret"
    assert outbound.headers["x-test"] == "yes"