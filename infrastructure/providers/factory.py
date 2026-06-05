"""Factories for provider clients and discovery services."""

from __future__ import annotations

from pathlib import Path

from infrastructure.config.env import ResolvedProviderConfig, load_provider_config
from infrastructure.providers.chat_completions import OpenAICompatibleChatCompletionsClient
from infrastructure.providers.http import AsyncHttpTransport, HttpTransport
from infrastructure.providers.model_catalog import ModelCatalogClient


def resolve_config(env_path: str | Path = ".env") -> ResolvedProviderConfig:
    return load_provider_config(env_path)


def create_model_client(
    env_path: str | Path = ".env",
    *,
    async_transport: AsyncHttpTransport | None = None,
) -> OpenAICompatibleChatCompletionsClient:
    resolved = load_provider_config(env_path)
    return OpenAICompatibleChatCompletionsClient(
        resolved,
        async_transport=async_transport,
    )


def create_model_catalog_client(
    env_path: str | Path = ".env",
    *,
    transport: HttpTransport | None = None,
) -> ModelCatalogClient:
    resolved = load_provider_config(env_path)
    return ModelCatalogClient(resolved, transport=transport)
