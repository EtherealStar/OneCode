"""供应商客户端与发现服务的工厂函数。"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from infrastructure.config.env import ResolvedProviderConfig, load_provider_config
from infrastructure.providers.chat_completions import (
    OpenAICompatibleChatCompletionsClient,
    build_async_openai_client,
)
from infrastructure.providers.http import HttpTransport
from infrastructure.providers.model_catalog import ModelCatalogClient


def resolve_config(env_path: str | Path = ".env") -> ResolvedProviderConfig:
    return load_provider_config(env_path)


def create_model_client(
    env_path: str | Path = ".env",
    *,
    sdk_client: AsyncOpenAI | None = None,
    http_client: Any | None = None,
) -> OpenAICompatibleChatCompletionsClient:
    """创建模型适配器，并由工厂注入配置好的 SDK client。

    `sdk_client` 供测试或热重载注入已构建的 SDK client；`http_client`
    供测试注入 `httpx.MockTransport`。两者都未提供时，工厂按
    `ResolvedProviderConfig` 构建 SDK client。
    """

    resolved = load_provider_config(env_path)
    if sdk_client is None:
        sdk_client = build_async_openai_client(resolved, http_client=http_client)
    return OpenAICompatibleChatCompletionsClient(resolved, sdk_client=sdk_client)


def create_model_catalog_client(
    env_path: str | Path = ".env",
    *,
    sdk_client: Any | None = None,
    http_client: Any | None = None,
    transport: HttpTransport | None = None,
) -> ModelCatalogClient:
    resolved = load_provider_config(env_path)
    return ModelCatalogClient(
        resolved,
        sdk_client=sdk_client,
        http_client=http_client,
        transport=transport,
    )


async def close_model_client(model_client: Any | None) -> None:
    """关闭应用拥有的模型客户端；重复调用安全。

    适配器暴露异步 `aclose()`；原始 SDK client 暴露异步 `close()`。
    先尝试 `aclose`，再尝试 `close`，并 await 返回的 awaitable。
    """

    if model_client is None:
        return
    for name in ("aclose", "close"):
        closer = getattr(model_client, name, None)
        if not callable(closer):
            continue
        result = closer()
        if inspect.isawaitable(result):
            await result
        return
