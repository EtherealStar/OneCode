"""通过 `httpx.MockTransport` 驱动 OpenAI SDK 的共享测试辅助。

以 ``sdk_test_support`` 名称导入，因为 pytest 会把 ``tests/`` 加入
``sys.path``。这些辅助函数产出与 OpenAI 兼容供应商一致的 SSE 传输格式，
使适配器测试走真实 SDK 传输、SSE 解码与类型化分片解析，而非手写假传输。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

import httpx
from openai import AsyncOpenAI, OpenAI

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.catalog import get_provider_definition

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


class Recorder:
    """记录每个出站请求的 `httpx` 可调用处理器。"""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    @property
    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[0].content)

    @property
    def last_body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def sse_body(chunks: Iterable[dict[str, Any]]) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def sse_response(
    chunks: Iterable[dict[str, Any]],
    *,
    status_code: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=sse_body(chunks),
        headers=SSE_HEADERS,
    )


def json_response(payload: Any, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload),
        headers=JSON_HEADERS,
    )


def error_response(
    status_code: int,
    message: str = "provider error",
    *,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps({"error": {"message": message}}),
        headers={**(headers or {}), **JSON_HEADERS},
    )


def text_chunk(content: str | None = None, *, finish_reason: str | None = None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    return _choice_chunk(delta, finish_reason=finish_reason)


def tool_call_chunk(
    index: int,
    *,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    function: dict[str, Any] = {}
    if name is not None:
        function["name"] = name
    if arguments is not None:
        function["arguments"] = arguments
    raw_tool_call: dict[str, Any] = {"index": index, "function": function}
    if call_id is not None:
        raw_tool_call["id"] = call_id
        raw_tool_call["type"] = "function"
    return _choice_chunk(
        {"tool_calls": [raw_tool_call]},
        finish_reason=finish_reason,
    )


def usage_chunk(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cached_tokens is not None:
        payload["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
    return {
        "id": "chunk",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test",
        "choices": [],
        "usage": payload,
    }


def _choice_chunk(
    delta: dict[str, Any],
    *,
    finish_reason: str | None,
) -> dict[str, Any]:
    return {
        "id": "chunk",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def async_sdk(
    config: ResolvedProviderConfig,
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncOpenAI:
    """构建由 `httpx.MockTransport` 支撑的 `AsyncOpenAI`。"""

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    return AsyncOpenAI(
        api_key=config.api_key or "local-placeholder",
        base_url=config.base_url,
        timeout=config.timeout_seconds,
        max_retries=0,
        default_headers=config.headers or None,
        http_client=client,
    )


def sync_sdk(
    *,
    base_url: str,
    handler: Callable[[httpx.Request], httpx.Response],
    api_key: str = "secret",
    timeout: float = 60.0,
    default_headers: dict[str, str] | None = None,
) -> OpenAI:
    """构建由 `httpx.MockTransport` 支撑的同步 `OpenAI`。"""

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return OpenAI(
        api_key=api_key or "local-placeholder",
        base_url=base_url,
        timeout=timeout,
        max_retries=0,
        default_headers=default_headers or None,
        http_client=client,
    )
