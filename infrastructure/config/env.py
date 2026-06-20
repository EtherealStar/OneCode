"""Provider configuration loaded only from a project .env file."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values

from infrastructure.providers.catalog import ProviderDefinition, get_provider_definition
from services.model.types import ProviderError


@dataclass(frozen=True)
class ResolvedProviderConfig:
    provider: ProviderDefinition
    provider_id: str
    display_name: str
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    headers: dict[str, str] = field(default_factory=dict)
    default_params: dict[str, Any] = field(default_factory=dict)
    models_path: str = "/models"
    chat_completions_path: str = "/chat/completions"


def load_provider_config(env_path: str | Path = ".env") -> ResolvedProviderConfig:
    # provider 配置只从项目 .env 读取；关闭插值可以让 API key 和
    # JSON 参数保持字面值。
    values = dotenv_values(Path(env_path), interpolate=False)
    if not values:
        raise ProviderError(
            f"Provider .env file is missing or empty: {env_path}",
            error_type="configuration_error",
        )

    provider_id = _required_string(values, "ONECODE_PROVIDER_ID")
    provider = _get_provider(provider_id)
    prefix = provider_env_prefix(provider.id)
    base_url = normalize_base_url(
        _optional_string(values, f"{prefix}_BASE_URL") or provider.base_url
    )
    if provider.requires_base_url and not base_url:
        raise ProviderError(
            f"Provider {provider.id!r} requires a base URL.",
            provider_id=provider.id,
            error_type="configuration_error",
        )
    if base_url:
        _validate_base_url(provider.id, base_url)

    headers = dict(provider.default_headers)
    headers.update(_string_mapping(values, "ONECODE_EXTRA_HEADERS"))
    if provider.api_key_required:
        secret = _required_string(values, f"{prefix}_API_KEY")
    else:
        secret = _optional_string(values, f"{prefix}_API_KEY") or ""

    return ResolvedProviderConfig(
        provider,
        provider.id,
        provider.display_name,
        base_url,
        _required_string(values, f"{prefix}_MODEL"),
        secret,
        timeout_seconds=_optional_float(values, "ONECODE_TIMEOUT_SECONDS", default=60.0),
        headers=headers,
        default_params=_object_mapping(values, "ONECODE_DEFAULT_PARAMS"),
        models_path=provider.models_path,
        chat_completions_path=provider.chat_completions_path,
    )


def provider_env_prefix(provider_id: str) -> str:
    return provider_id.upper().replace("-", "_")


def normalize_base_url(base_url: str | None) -> str:
    if base_url is None:
        return ""
    return base_url.strip().rstrip("/")


def _required_string(values: dict[str, str | None], key: str) -> str:
    value = _optional_string(values, key)
    if not value:
        raise ProviderError(
            f"Provider .env requires {key}.",
            error_type="configuration_error",
        )
    return value


def _optional_string(values: dict[str, str | None], key: str) -> str | None:
    value = values.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _optional_float(values: dict[str, str | None], key: str, *, default: float) -> float:
    raw = _optional_string(values, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ProviderError(
            f"Provider .env field {key} must be a number.",
            error_type="configuration_error",
        ) from exc


def _string_mapping(values: dict[str, str | None], key: str) -> dict[str, str]:
    value = _json_object(values, key)
    result: dict[str, str] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_key, str) or not isinstance(item_value, str):
            raise ProviderError(
                f"Provider .env field {key} must contain string values.",
                error_type="configuration_error",
            )
        result[item_key] = item_value
    return result


def _object_mapping(values: dict[str, str | None], key: str) -> dict[str, Any]:
    return _json_object(values, key)


def _json_object(values: dict[str, str | None], key: str) -> dict[str, Any]:
    raw = _optional_string(values, key)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"Provider .env field {key} must be a JSON object.",
            error_type="configuration_error",
        ) from exc
    if not isinstance(value, dict):
        raise ProviderError(
            f"Provider .env field {key} must be a JSON object.",
            error_type="configuration_error",
        )
    return value


def _get_provider(provider_id: str) -> ProviderDefinition:
    try:
        return get_provider_definition(provider_id)
    except KeyError as exc:
        raise ProviderError(
            f"Unknown provider id: {provider_id}",
            provider_id=provider_id,
            error_type="configuration_error",
        ) from exc


def _validate_base_url(provider_id: str, base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProviderError(
            f"Provider {provider_id!r} has an invalid base URL.",
            provider_id=provider_id,
            error_type="configuration_error",
        )
