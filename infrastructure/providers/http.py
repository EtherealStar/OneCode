"""Small JSON HTTP transport for OpenAI-compatible providers."""

from __future__ import annotations

import json
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from services.model.types import ProviderError


class HttpTransport(Protocol):
    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...


class UrllibHttpTransport:
    def __init__(self, *, provider_id: str | None = None) -> None:
        self.provider_id = provider_id

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        return self._request_json(request, timeout_seconds)

    def get_json(
        self,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        request = Request(url, headers=headers, method="GET")
        return self._request_json(request, timeout_seconds)

    def _request_json(self, request: Request, timeout_seconds: float) -> dict[str, Any]:
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw_body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise provider_error_from_http_status(
                exc.code,
                _read_error_body(exc),
                provider_id=self.provider_id,
            ) from exc
        except URLError as exc:
            raise ProviderError(
                "Provider network error.",
                provider_id=self.provider_id,
                error_type="network_error",
                retryable=True,
            ) from exc
        except TimeoutError as exc:
            raise ProviderError(
                "Provider request timed out.",
                provider_id=self.provider_id,
                error_type="network_error",
                retryable=True,
            ) from exc

        return parse_json_object(raw_body, provider_id=self.provider_id)


def parse_json_object(raw_body: str, *, provider_id: str | None = None) -> dict[str, Any]:
    try:
        value = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            "Provider returned invalid JSON.",
            provider_id=provider_id,
            error_type="invalid_response",
        ) from exc
    if not isinstance(value, dict):
        raise ProviderError(
            "Provider returned a non-object JSON response.",
            provider_id=provider_id,
            error_type="invalid_response",
        )
    return value


def provider_error_from_http_status(
    status_code: int,
    raw_body: str | None = None,
    *,
    provider_id: str | None = None,
) -> ProviderError:
    error_type = _error_type_for_status(status_code)
    # retryable 保持 provider-neutral，后续主循环可以映射到 transition，
    # 无需理解 HTTP 状态细节。
    retryable = status_code == 429 or status_code >= 500
    message = _extract_error_message(raw_body) or f"Provider HTTP error {status_code}."
    return ProviderError(
        message,
        provider_id=provider_id,
        status_code=status_code,
        error_type=error_type,
        retryable=retryable,
    )


def _error_type_for_status(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication_error"
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "server_error"
    return "invalid_response"


def _extract_error_message(raw_body: str | None) -> str | None:
    if not raw_body:
        return None
    try:
        value = json.loads(raw_body)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(value.get("message"), str):
        return value["message"]
    return None


def _read_error_body(error: HTTPError) -> str:
    try:
        return error.read().decode("utf-8")
    except Exception:
        return ""
