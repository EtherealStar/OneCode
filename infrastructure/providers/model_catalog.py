"""基于 OpenAI 兼容 /models 端点的供应商模型发现服务。"""

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


def fetch_models_for_connect(
    provider: "ProviderDefinition",
    api_key: str,
    base_url: str | None = None,
    *,
    transport: HttpTransport | None = None,
) -> tuple[ProviderModel, ...]:
    """在没有完整配置的情况下为 /connect 向导拉取模型列表。

    对于 Ollama（models_path == "/api/tags"），请求 Ollama 专用端点并解析其响应格式。

    对于其他供应商，首先尝试 {base_url}/v1/models（除非 base_url 已以 /v1 结尾），
    随后尝试 {base_url}/models，以此自动探测模型端点。
    """

    from infrastructure.providers.catalog import ProviderDefinition  # noqa: F811

    effective_base_url = (base_url or provider.base_url).rstrip("/")
    if not effective_base_url:
        raise ProviderError(
            "A base URL is required to fetch models.",
            provider_id=provider.id,
            error_type="configuration_error",
        )

    http = transport or UrllibHttpTransport(provider_id=provider.id)
    headers: dict[str, str] = dict(provider.default_headers)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    is_ollama = provider.models_path == "/api/tags"

    if is_ollama:
        return _fetch_ollama_models(effective_base_url, headers, http)

    return _fetch_openai_models_with_probe(
        effective_base_url, headers, http, provider_id=provider.id,
    )


def _fetch_ollama_models(
    base_url: str,
    headers: dict[str, str],
    transport: HttpTransport,
) -> tuple[ProviderModel, ...]:
    """从 Ollama /api/tags 端点拉取模型列表。"""

    url = f"{base_url}/api/tags"
    response = transport.get_json(url, headers, 30.0)
    return _parse_ollama_models(response)


def _parse_ollama_models(response: dict[str, Any]) -> tuple[ProviderModel, ...]:
    """解析 Ollama /api/tags 响应格式。

    Ollama 返回 {"models": [{"name": "...", "model": "...", ...}]}。
    """

    raw_models = response.get("models")
    if not isinstance(raw_models, list):
        return ()

    models: list[ProviderModel] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        # Ollama 使用 model 作为规范 ID，name 作为显示名称。
        model_id = item.get("model") or item.get("name")
        if not isinstance(model_id, str) or not model_id:
            continue
        display_name = item.get("name")
        models.append(
            ProviderModel(
                id=model_id,
                display_name=display_name if isinstance(display_name, str) else None,
                raw=dict(item),
            )
        )
    return tuple(sorted(models, key=lambda m: m.id))


def _fetch_openai_models_with_probe(
    base_url: str,
    headers: dict[str, str],
    transport: HttpTransport,
    *,
    provider_id: str,
) -> tuple[ProviderModel, ...]:
    """优先尝试 /v1/models，失败后尝试 /models。

    若 base_url 已以 /v1 结尾，则仅尝试该 base 下的 /models，
    以避免冗余探测 /v1/v1/models。
    """

    urls_to_try: list[str] = []
    if not base_url.rstrip("/").endswith("/v1"):
        urls_to_try.append(f"{base_url}/v1/models")
    urls_to_try.append(f"{base_url}/models")

    last_error: Exception | None = None
    for url in urls_to_try:
        try:
            response = transport.get_json(url, headers, 30.0)
            return _parse_models(response, provider_id=provider_id)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    raise ProviderError(
        "Failed to discover models endpoint.",
        provider_id=provider_id,
        error_type="network_error",
    )


def test_model_connection(
    provider: "ProviderDefinition",
    api_key: str,
    model: str,
    base_url: str | None = None,
    *,
    transport: HttpTransport | None = None,
) -> str | None:
    """发送极简的聊天补全请求以验证模型是否可连通。

    成功时返回 None，失败时返回错误信息字符串。
    """

    effective_base_url = (base_url or provider.base_url).rstrip("/")
    if not effective_base_url:
        return "未提供 base URL。"

    http = transport or UrllibHttpTransport(provider_id=provider.id)
    headers: dict[str, str] = dict(provider.default_headers)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 确定 chat completions 端点。
    chat_path = provider.chat_completions_path or "/chat/completions"
    # 对于 Ollama，聊天端点为 /api/chat。
    if provider.models_path == "/api/tags":
        chat_path = "/api/chat"

    url = _join_url(effective_base_url, chat_path)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 1,
        "stream": False,
    }

    try:
        http.post_json(url, {**headers, "Content-Type": "application/json"}, payload, 30.0)
        return None
    except ProviderError as exc:
        return exc.message
    except Exception as exc:  # noqa: BLE001
        return str(exc)
