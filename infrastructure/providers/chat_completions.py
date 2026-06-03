"""OpenAI Chat Completions compatible model client."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.http import HttpTransport, UrllibHttpTransport
from services.context.snapshot import ContextSnapshot
from services.model.types import LLMResponse, ModelUsage, ProviderError
from services.tools.types import ToolCall


class OpenAICompatibleChatCompletionsClient:
    def __init__(
        self,
        config: ResolvedProviderConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibHttpTransport(provider_id=config.provider_id)

    def send(self, snapshot: ContextSnapshot) -> LLMResponse:
        if not self.config.model:
            raise self._configuration_error("A model must be configured before calling chat completions.")
        payload = self._build_payload(snapshot)
        response = self.transport.post_json(
            _join_url(self.config.base_url, self.config.chat_completions_path),
            self._headers(),
            payload,
            self.config.timeout_seconds,
        )
        return self._parse_response(response)

    def _build_payload(self, snapshot: ContextSnapshot) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if snapshot.system_prompt:
            messages.append({"role": "system", "content": snapshot.system_prompt})
        messages.extend(snapshot.messages)

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            **self.config.default_params,
        }
        if snapshot.tool_schemas:
            payload["tools"] = list(snapshot.tool_schemas)
        return payload

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise self._configuration_error("An API key must be configured before calling the provider.")
        return {
            **self.config.headers,
            "Authorization": f"Bearer {self.config.api_key}",
        }

    def _parse_response(self, response: dict[str, Any]) -> LLMResponse:
        choice = _first_choice(response, provider_id=self.config.provider_id)
        message = choice.get("message")
        if not isinstance(message, dict):
            raise self._invalid_response("Provider response choice is missing message.")

        raw_tool_calls = message.get("tool_calls")
        tool_calls = self._parse_tool_calls(raw_tool_calls)
        assistant_message = _assistant_message(message, raw_tool_calls)

        return LLMResponse(
            assistant_message=assistant_message,
            final_text=_content_to_text(message.get("content")),
            tool_calls=tool_calls,
            stop_reason=_string_or_none(choice.get("finish_reason")),
            usage=_parse_usage(response.get("usage")),
        )

    def _parse_tool_calls(self, raw_tool_calls: Any) -> tuple[ToolCall, ...]:
        if raw_tool_calls is None:
            return ()
        if not isinstance(raw_tool_calls, list):
            raise self._invalid_response("Provider tool_calls field must be a list.")

        parsed: list[ToolCall] = []
        for index, raw_tool_call in enumerate(raw_tool_calls):
            if not isinstance(raw_tool_call, dict):
                raise self._invalid_response("Provider tool call must be an object.")
            function = raw_tool_call.get("function")
            if not isinstance(function, dict):
                raise self._invalid_response("Provider tool call is missing function.")
            name = function.get("name")
            if not isinstance(name, str) or not name:
                raise self._invalid_response("Provider tool call is missing function name.")
            parsed.append(
                ToolCall(
                    id=_string_or_none(raw_tool_call.get("id")) or f"call_{index}",
                    name=name,
                    input=self._parse_arguments(function.get("arguments")),
                )
            )
        return tuple(parsed)

    def _parse_arguments(self, arguments: Any) -> dict[str, Any]:
        if arguments in (None, ""):
            return {}
        if isinstance(arguments, dict):
            return arguments
        if not isinstance(arguments, str):
            raise self._invalid_tool_arguments("Tool arguments must be a JSON object string.")
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise self._invalid_tool_arguments("Tool arguments are not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise self._invalid_tool_arguments("Tool arguments JSON must be an object.")
        return parsed

    def _configuration_error(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="configuration_error",
        )

    def _invalid_response(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="invalid_response",
        )

    def _invalid_tool_arguments(self, message: str) -> ProviderError:
        return ProviderError(
            message,
            provider_id=self.config.provider_id,
            error_type="invalid_tool_arguments",
        )


def _first_choice(response: dict[str, Any], *, provider_id: str) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(
            "Provider response is missing choices.",
            provider_id=provider_id,
            error_type="invalid_response",
        )
    choice = choices[0]
    if not isinstance(choice, dict):
        raise ProviderError(
            "Provider response choice must be an object.",
            provider_id=provider_id,
            error_type="invalid_response",
        )
    return choice


def _assistant_message(
    message: dict[str, Any],
    raw_tool_calls: Any,
) -> dict[str, Any]:
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content", ""),
    }
    if raw_tool_calls:
        assistant_message["tool_calls"] = raw_tool_calls
    return assistant_message


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _parse_usage(usage: Any) -> ModelUsage | None:
    if usage is None:
        return None
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    return ModelUsage(
        input_tokens=_int_or_zero(usage.get("prompt_tokens")),
        output_tokens=_int_or_zero(usage.get("completion_tokens")),
        cache_read_input_tokens=_int_or_zero(prompt_details.get("cached_tokens")),
    )


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0
