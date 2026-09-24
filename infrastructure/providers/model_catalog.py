"""基于 OpenAI 兼容 /models 端点的供应商模型发现服务。

标准 `/models` 请求与 `/connect` 的标准 Chat Completions 连通性探测由
OpenAI Python SDK 发出；OneCode 仍决定候选 base URL、结果排序、失败 fallback
以及 Ollama 原生 `/api/tags`、`/api/chat` 等非标准端点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.chat_completions import (
    SDK_PLACEHOLDER_API_KEY,
    _join_url,
)
from infrastructure.providers.http import HttpTransport, UrllibHttpTransport
from infrastructure.providers.sdk_errors import provider_error_from_sdk_exception
from services.model.types import ProviderError


@dataclass(frozen=True)
class ProviderModel:
    id: str
    display_name: str | None = None
    owned_by: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def build_sync_openai_client(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: float = 60.0,
    headers: dict[str, str] | None = None,
    http_client: Any | None = None,
) -> OpenAI:
    """创建用于同步模型发现/探测的 SDK client；SDK 不做隐藏重试。"""

    return OpenAI(
        api_key=api_key or SDK_PLACEHOLDER_API_KEY,
        base_url=base_url or None,
        timeout=timeout_seconds,
        max_retries=0,
        default_headers=headers or None,
        http_client=http_client,
    )


class ModelCatalogClient:
    def __init__(
        self,
        config: ResolvedProviderConfig,
        *,
        sdk_client: OpenAI | None = None,
        http_client: Any | None = None,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibHttpTransport(provider_id=config.provider_id)
        self.sdk_client = sdk_client or build_sync_openai_client(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            headers=config.headers,
            http_client=http_client,
        )

    def list_models(self) -> tuple[ProviderModel, ...]:
        if not self.config.api_key:
            raise ProviderError(
                "An API key must be configured before listing provider models.",
                provider_id=self.config.provider_id,
                error_type="configuration_error",
            )
        try:
            page = self.sdk_client.models.list()
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - 在 provider 边界统一归一化
            raise provider_error_from_sdk_exception(
                exc,
                provider_id=self.config.provider_id,
            ) from exc
        return _provider_models_from_sdk(page.data)


def fetch_models_for_connect(
    provider: "ProviderDefinition",
    api_key: str,
    base_url: str | None = None,
    *,
    transport: HttpTransport | None = None,
    http_client: Any | None = None,
) -> tuple[ProviderModel, ...]:
    """在没有完整配置的情况下为 /connect 向导拉取模型列表。

    对于 Ollama（models_path == "/api/tags"），请求 Ollama 专用端点并解析其响应格式。

    对于其他供应商，用 SDK Models resource 依次尝试候选 base URL，
    自动探测 `{base_url}/v1/models` 与 `{base_url}/models`。
    """

    from infrastructure.providers.catalog import ProviderDefinition  # noqa: F811

    effective_base_url = (base_url or provider.base_url).rstrip("/")
    if not effective_base_url:
        raise ProviderError(
            "A base URL is required to fetch models.",
            provider_id=provider.id,
            error_type="configuration_error",
        )

    if provider.models_path == "/api/tags":
        http = transport or UrllibHttpTransport(provider_id=provider.id)
        headers: dict[str, str] = dict(provider.default_headers)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return _fetch_ollama_models(effective_base_url, headers, http)

    return _fetch_openai_models_with_probe(
        provider,
        api_key,
        effective_base_url,
        http_client=http_client,
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
    provider: "ProviderDefinition",
    api_key: str,
    base_url: str,
    *,
    http_client: Any | None = None,
) -> tuple[ProviderModel, ...]:
    """用 SDK 依次尝试候选 base URL。

    若 base_url 已以 /v1 结尾，则仅尝试该 base 下的 /models，
    以避免冗余探测 /v1/v1/models。
    """

    candidates: list[str] = []
    if not base_url.rstrip("/").endswith("/v1"):
        candidates.append(f"{base_url}/v1")
    candidates.append(base_url)

    own_client = http_client is None
    last_error: ProviderError | None = None
    for candidate in candidates:
        client = build_sync_openai_client(
            api_key=api_key,
            base_url=candidate,
            timeout_seconds=30.0,
            headers=provider.default_headers,
            http_client=http_client,
        )
        try:
            page = client.models.list()
            return _provider_models_from_sdk(page.data)
        except Exception as exc:  # noqa: BLE001 - 记录后继续尝试下一个候选
            last_error = provider_error_from_sdk_exception(
                exc,
                provider_id=provider.id,
            )
            continue
        finally:
            if own_client:
                client.close()

    if last_error is not None:
        raise last_error
    raise ProviderError(
        "Failed to discover models endpoint.",
        provider_id=provider.id,
        error_type="network_error",
    )


def _provider_models_from_sdk(data: Any) -> tuple[ProviderModel, ...]:
    """把 SDK `Model` 对象整理成排序后的 `ProviderModel`。"""

    models: list[ProviderModel] = []
    for item in data or ():
        model_id = getattr(item, "id", None)
        if not isinstance(model_id, str) or not model_id:
            continue
        raw = item.model_dump() if hasattr(item, "model_dump") else {}
        display_name = getattr(item, "display_name", None)
        if not isinstance(display_name, str):
            display_name = raw.get("display_name") if isinstance(raw, dict) else None
        owned_by = getattr(item, "owned_by", None)
        models.append(
            ProviderModel(
                id=model_id,
                display_name=display_name if isinstance(display_name, str) else None,
                owned_by=owned_by if isinstance(owned_by, str) else None,
                raw=raw if isinstance(raw, dict) else {},
            )
        )
    return tuple(sorted(models, key=lambda model: model.id))


def test_model_connection(
    provider: "ProviderDefinition",
    api_key: str,
    model: str,
    base_url: str | None = None,
    *,
    transport: HttpTransport | None = None,
    http_client: Any | None = None,
) -> str | None:
    """发送极简的聊天补全请求以验证模型是否可连通。

    成功时返回 None，失败时返回错误信息字符串。
    """

    effective_base_url = (base_url or provider.base_url).rstrip("/")
    if not effective_base_url:
        return "未提供 base URL。"

    headers: dict[str, str] = dict(provider.default_headers)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Ollama 的聊天端点为 /api/chat，走隔离的通用 HTTP 请求。
    if provider.models_path == "/api/tags":
        http = transport or UrllibHttpTransport(provider_id=provider.id)
        url = _join_url(effective_base_url, "/api/chat")
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
        except Exception as exc:  # noqa: BLE001 - 探测失败需返回可读信息
            return str(exc)

    own_client = http_client is None
    client = build_sync_openai_client(
        api_key=api_key,
        base_url=effective_base_url,
        timeout_seconds=30.0,
        headers=provider.default_headers,
        http_client=http_client,
    )
    try:
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=1,
            stream=False,
        )
        return None
    except ProviderError as exc:
        return exc.message
    except Exception as exc:  # noqa: BLE001 - 探测失败需返回可读信息
        return provider_error_from_sdk_exception(exc, provider_id=provider.id).message
    finally:
        if own_client:
            client.close()
