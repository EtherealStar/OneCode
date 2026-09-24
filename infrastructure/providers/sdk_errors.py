"""OpenAI Python SDK 异常到供应商中立 `ProviderError` 的归一化。

SDK 类型与异常只允许出现在 infrastructure 层。模型流适配器与模型发现
在调用边界把 SDK 的连接、超时、状态码和响应异常转成本模块定义的
`ProviderError`，使 `core/`、`services/` 与 UI 不必理解 SDK 细节。
"""

from __future__ import annotations

import openai

from infrastructure.providers.http import provider_error_from_http_status
from services.model.types import ProviderError


def provider_error_from_sdk_exception(
    exc: BaseException,
    *,
    provider_id: str | None = None,
) -> ProviderError:
    """把 SDK 异常映射成 `ProviderError`，保留状态码与重试语义。"""

    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(
            "Provider request timed out.",
            provider_id=provider_id,
            error_type="timeout_error",
            retryable=True,
        )
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(
            "Provider network error.",
            provider_id=provider_id,
            error_type="network_error",
            retryable=True,
        )
    if isinstance(exc, openai.APIStatusError):
        return _provider_error_from_status(exc, provider_id=provider_id)
    return ProviderError(
        "Provider returned an invalid response.",
        provider_id=provider_id,
        error_type="invalid_response",
    )


def _provider_error_from_status(
    exc: "openai.APIStatusError",
    *,
    provider_id: str | None,
) -> ProviderError:
    status_code = exc.status_code
    response = getattr(exc, "response", None)
    raw_body = _response_text(response)
    error = provider_error_from_http_status(
        status_code,
        raw_body,
        provider_id=provider_id,
    )
    retry_after_seconds = _parse_retry_after(getattr(response, "headers", None))
    if retry_after_seconds is None:
        return error
    return ProviderError(
        error.message,
        provider_id=error.provider_id,
        status_code=error.status_code,
        error_type=error.error_type,
        retryable=error.retryable,
        retry_after_seconds=retry_after_seconds,
    )


def _response_text(response: object) -> str | None:
    if response is None:
        return None
    try:
        return response.text
    except Exception:  # noqa: BLE001 - 响应正文仅用于提取错误消息
        return None


def _parse_retry_after(headers: object) -> float | None:
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after")
    except Exception:  # noqa: BLE001 - headers 可能不支持 get
        return None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return seconds
