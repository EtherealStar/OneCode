"""兼容 OpenAI Chat Completions 的模型客户端。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from infrastructure.config.env import ResolvedProviderConfig
from infrastructure.providers.http import (
    AsyncHttpTransport,
    HttpxAsyncHttpTransport,
)
from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent
from services.model.types import ModelUsage, ProviderError
from services.tools.types import ToolCall


# SDK 3.18.0 Chat Completions `create()` 已声明的可选参数名。
# 命中的 default_params 字段按 SDK 具名参数传递，其余经 extra_body 传递。
SDK_CHAT_OPTION_NAMES = frozenset(
    {
        "audio",
        "frequency_penalty",
        "function_call",
        "functions",
        "logit_bias",
        "logprobs",
        "max_completion_tokens",
        "max_tokens",
        "metadata",
        "modalities",
        "moderation",
        "n",
        "parallel_tool_calls",
        "prediction",
        "presence_penalty",
        "prompt_cache_key",
        "prompt_cache_options",
        "prompt_cache_retention",
        "reasoning_effort",
        "response_format",
        "safety_identifier",
        "seed",
        "service_tier",
        "stop",
        "store",
        "stream_options",
        "temperature",
        "tool_choice",
        "top_logprobs",
        "top_p",
        "user",
        "verbosity",
        "web_search_options",
    }
)

# 运行时保留字段：由 OneCode 决定，配置不得覆盖。
_RESERVED_CHAT_FIELDS = frozenset({"model", "messages", "tools", "stream"})


@dataclass(frozen=True)
class ChatCompletionsRequest:
    """SDK `chat.completions.create()` 的请求投影。"""

    named: dict[str, Any]
    extra_body: dict[str, Any]

    def to_body(self) -> dict[str, Any]:
        return {**self.named, **self.extra_body}


def build_chat_completions_request(
    config: ResolvedProviderConfig,
    snapshot: ContextSnapshot,
    *,
    stream: bool = True,
) -> ChatCompletionsRequest:
    """把配置与上下文投影成 SDK 请求参数。

    运行时决定 `model`、`messages`、`tools`、`stream` 与输出上限；
    `default_params` 中 SDK 已声明的可选参数是具名参数，其余字段走
    `extra_body`，且无法覆盖运行时保留字段。
    """

    named: dict[str, Any] = {
        "model": config.model,
        "messages": _project_messages(snapshot),
        "stream": stream,
    }
    if snapshot.tool_schemas:
        named["tools"] = list(snapshot.tool_schemas)

    extra_body: dict[str, Any] = {}
    for key, value in config.default_params.items():
        if key in _RESERVED_CHAT_FIELDS:
            continue
        if key in SDK_CHAT_OPTION_NAMES:
            named[key] = value
        else:
            extra_body[key] = value

    max_output_tokens = _requested_max_output_tokens(snapshot)
    if max_output_tokens is not None:
        named["max_tokens"] = max_output_tokens

    return ChatCompletionsRequest(named=named, extra_body=extra_body)


@dataclass
class _ToolCallAccumulator:
    index: int
    call_id: str | None = None
    name: str = ""
    arguments: str = ""
    completed: bool = False


class OpenAICompatibleChatCompletionsClient:
    def __init__(
        self,
        config: ResolvedProviderConfig,
        *,
        async_transport: AsyncHttpTransport | None = None,
    ) -> None:
        self.config = config
        self.async_transport = async_transport or HttpxAsyncHttpTransport(
            provider_id=config.provider_id
        )

    async def stream(
        self,
        snapshot: ContextSnapshot,
    ) -> AsyncIterator[ModelStreamEvent]:
        if not self.config.model:
            raise self._configuration_error("A model must be configured before calling chat completions.")
        payload = {**self._build_payload(snapshot), "stream": True}
        final_text_parts: list[str] = []
        tool_accumulators: dict[int, _ToolCallAccumulator] = {}
        stop_reason: str | None = None
        usage: ModelUsage | None = None
        emitted_completed_tool_ids: set[str] = set()

        async for chunk in self.async_transport.stream_json_lines(
            _join_url(self.config.base_url, self.config.chat_completions_path),
            self._headers(),
            payload,
            self.config.timeout_seconds,
        ):
            chunk_usage = _parse_usage(chunk.get("usage"))
            if chunk_usage is not None:
                usage = chunk_usage
                yield ModelStreamEvent.usage_event(chunk_usage)

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                raise self._invalid_response("Provider stream choice must be an object.")
            finish_reason = _string_or_none(choice.get("finish_reason"))
            if finish_reason is not None:
                stop_reason = finish_reason
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue

            content = delta.get("content")
            if isinstance(content, str) and content:
                final_text_parts.append(content)
                yield ModelStreamEvent.content_delta(content)

            raw_tool_calls = delta.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for raw_delta in raw_tool_calls:
                    if not isinstance(raw_delta, dict):
                        continue
                    accumulator = _update_tool_accumulator(
                        tool_accumulators,
                        raw_delta,
                    )
                    yield ModelStreamEvent.tool_call_delta(
                        metadata={
                            "index": accumulator.index,
                            "id": accumulator.call_id,
                            "name": accumulator.name,
                            "arguments_delta_chars": _arguments_delta_chars(raw_delta),
                        }
                    )

        tool_calls = self._completed_tool_calls(tool_accumulators)
        for tool_call in tool_calls:
            if tool_call.id in emitted_completed_tool_ids:
                continue
            emitted_completed_tool_ids.add(tool_call.id)
            yield ModelStreamEvent.tool_call_completed(tool_call)
        final_text = "".join(final_text_parts)
        assistant_message = _assistant_message_from_stream(final_text, tool_accumulators)
        yield ModelStreamEvent.message_completed(
            assistant_message=assistant_message,
            final_text=final_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            output_interrupted=_is_output_interrupted_stop_reason(stop_reason),
        )

    def _build_payload(self, snapshot: ContextSnapshot) -> dict[str, Any]:
        return build_chat_completions_request(self.config, snapshot).to_body()

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise self._configuration_error("An API key must be configured before calling the provider.")
        return {
            **self.config.headers,
            "Authorization": f"Bearer {self.config.api_key}",
        }

    def _completed_tool_calls(
        self,
        accumulators: dict[int, _ToolCallAccumulator],
    ) -> tuple[ToolCall, ...]:
        parsed: list[ToolCall] = []
        for index in sorted(accumulators):
            accumulator = accumulators[index]
            if not accumulator.name:
                continue
            parsed.append(
                ToolCall(
                    id=accumulator.call_id or f"call_{index}",
                    name=accumulator.name,
                    input=self._parse_arguments(accumulator.arguments),
                )
            )
        return tuple(parsed)

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


def _assistant_message_from_stream(
    final_text: str,
    accumulators: dict[int, _ToolCallAccumulator],
) -> dict[str, Any]:
    raw_tool_calls = [
        {
            "id": accumulator.call_id or f"call_{index}",
            "type": "function",
            "function": {
                "name": accumulator.name,
                "arguments": accumulator.arguments,
            },
        }
        for index, accumulator in sorted(accumulators.items())
        if accumulator.name
    ]
    return _assistant_message(
        {"content": final_text},
        raw_tool_calls if raw_tool_calls else None,
    )


def _update_tool_accumulator(
    accumulators: dict[int, _ToolCallAccumulator],
    raw_delta: dict[str, Any],
) -> _ToolCallAccumulator:
    raw_index = raw_delta.get("index")
    index = raw_index if isinstance(raw_index, int) else len(accumulators)
    accumulator = accumulators.setdefault(
        index,
        _ToolCallAccumulator(index=index),
    )
    call_id = _string_or_none(raw_delta.get("id"))
    if call_id:
        accumulator.call_id = call_id
    function = raw_delta.get("function")
    if isinstance(function, dict):
        name = _string_or_none(function.get("name"))
        if name:
            accumulator.name += name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            accumulator.arguments += arguments
    return accumulator


def _arguments_delta_chars(raw_delta: dict[str, Any]) -> int:
    function = raw_delta.get("function")
    if not isinstance(function, dict):
        return 0
    arguments = function.get("arguments")
    return len(arguments) if isinstance(arguments, str) else 0


def _requested_max_output_tokens(snapshot: ContextSnapshot) -> int | None:
    request_overrides = snapshot.usage_hints.get("request_overrides")
    if not isinstance(request_overrides, dict):
        return None
    max_output_tokens = request_overrides.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        return max_output_tokens
    return None


def _project_messages(snapshot: ContextSnapshot) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if snapshot.system_prompt:
        messages.append({"role": "system", "content": snapshot.system_prompt})
    messages.extend(snapshot.messages)
    projected: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "tool_result":
            # OneCode 内部存 provider-neutral 的 tool_result；
            # Chat Completions wire format 需要 role="tool"。
            projected.append(
                {
                    "role": "tool",
                    "tool_call_id": message.get("tool_call_id", ""),
                    "content": message.get("content", ""),
                }
            )
        else:
            projected.append(dict(message))
    return projected


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


def _is_output_interrupted_stop_reason(stop_reason: str | None) -> bool:
    return stop_reason in {"length", "max_tokens", "max_output_tokens"}
