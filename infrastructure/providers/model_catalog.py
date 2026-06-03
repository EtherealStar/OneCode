"""Provider model discovery over OpenAI-compatible /models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.chat_completions import _join_url
from infrastructure.providers.http import HttpTransport, UrllibHttpTransport
from services.model.types import ProviderError


@dataclass(frozen=True)
class ProviderModel:
    id: str
    display_name: str | None = None
    owned_by: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ModelCatalogClient:
    def __init__(
        self,
        config: ResolvedProviderConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibHttpTransport(provider_id=config.provider_id)

    def list_models(self) -> tuple[ProviderModel, ...]:
        if not self.config.api_key:
            raise ProviderError(
                "An API key must be configured before listing provider models.",
                provider_id=self.config.provider_id,
                error_type="configuration_error",
            )
        response = self.transport.get_json(
            _join_url(self.config.base_url, self.config.models_path),
            {
                **self.config.headers,
                "Authorization": f"Bearer {self.config.api_key}",
            },
            self.config.timeout_seconds,
        )
        return _parse_models(response, provider_id=self.config.provider_id)


def _parse_models(
    response: dict[str, Any],
    *,
    provider_id: str,
) -> tuple[ProviderModel, ...]:
    data = response.get("data")
    if not isinstance(data, list):
        raise ProviderError(
            "Provider models response is missing data list.",
            provider_id=provider_id,
            error_type="invalid_response",
        )

    models: list[ProviderModel] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        display_name = item.get("display_name")
        owned_by = item.get("owned_by")
        models.append(
            ProviderModel(
                id=model_id,
                display_name=display_name if isinstance(display_name, str) else None,
                owned_by=owned_by if isinstance(owned_by, str) else None,
                raw=dict(item),
            )
        )
    return tuple(sorted(models, key=lambda model: model.id))
