from __future__ import annotations

import json
import http.client
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .errors import ContextLimitError, RateLimitError, classify_provider_error


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    assistant_message: dict
    final_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict | None = None
    output_interrupted: bool = False


class ModelClient(Protocol):
    def send(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_output_tokens: int,
    ) -> LLMResponse:
        ...

    def summarize(self, *, messages: list[dict], focus: str | None = None) -> str:
        ...


class ChatCompletionsClient:
    """Chat Completions protocol client using only the Python stdlib."""

    def __init__(self, *, model: str, api_key: str | None = None, base_url: str | None = None):
        if not base_url:
            raise ValueError("ONECODE_BASE_URL is required. Set it to your chat-completions server.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    @property
    def chat_completions_url(self) -> str:
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/v1/chat/completions"

    def send(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_output_tokens: int,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": self._to_openai_messages(system, messages),
            "tools": [self._to_openai_tool(tool) for tool in tools],
            "tool_choice": "auto",
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        data = self._post_json(self.chat_completions_url, payload)
        choice = data["choices"][0]
        message = choice.get("message", {})
        content_blocks: list[dict] = []
        final_text = message.get("content") or ""
        if final_text:
            content_blocks.append({"type": "text", "text": final_text})
        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                parsed_args = json.loads(raw_args)
            except json.JSONDecodeError:
                parsed_args = {"_raw_arguments": raw_args}
            tool_call = ToolCall(
                id=raw_call.get("id") or function.get("name") or "tool_call",
                name=function.get("name") or "",
                input=parsed_args,
            )
            tool_calls.append(tool_call)
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.input,
                }
            )
        finish_reason = choice.get("finish_reason")
        return LLMResponse(
            assistant_message={"role": "assistant", "content": content_blocks},
            final_text=final_text,
            tool_calls=tool_calls,
            stop_reason=finish_reason,
            usage=data.get("usage"),
            output_interrupted=finish_reason == "length",
        )

    def summarize(self, *, messages: list[dict], focus: str | None = None) -> str:
        focus_text = f"\nFocus: {focus}\n" if focus else ""
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": "Summarize coding-agent history. Respond with text only.",
                },
                {
                    "role": "user",
                    "content": (
                        "Preserve current goal, key findings, changed files, user constraints, "
                        "and concrete next steps."
                        f"{focus_text}\n\n{json.dumps(messages, ensure_ascii=False, default=str)}"
                    ),
                },
            ],
            "max_tokens": 2_000,
            "stream": False,
        }
        data = self._post_json(self.chat_completions_url, payload)
        return (data["choices"][0].get("message", {}).get("content") or "").strip()

    def _post_json(self, url: str, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            retry_after = exc.headers.get("Retry-After")
            if exc.code == 429:
                raise RateLimitError(body or str(exc), float(retry_after) if retry_after else None) from exc
            if exc.code == 413:
                raise ContextLimitError(body or str(exc)) from exc
            classified = classify_provider_error(Exception(body or str(exc)))
            if classified:
                raise classified from exc
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            classified = classify_provider_error(exc)
            if classified:
                raise classified from exc
            raise
        except (http.client.RemoteDisconnected, ConnectionResetError) as exc:
            raise RuntimeError(
                "Chat completions server closed the connection without an HTTP response. "
                "Check ONECODE_BASE_URL, http/https scheme, reverse proxy routing, and whether "
                "the server supports POST /v1/chat/completions with tool-calling payloads."
            ) from exc

    @staticmethod
    def _to_openai_tool(tool: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
        converted = [{"role": "system", "content": system}]
        pending_tool_calls: list[dict] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "assistant":
                text_parts: list[str] = []
                tool_calls: list[dict] = []
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    if block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name"),
                                    "arguments": json.dumps(
                                        block.get("input") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                        )
                openai_message = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_calls:
                    openai_message["tool_calls"] = tool_calls
                    pending_tool_calls.extend(tool_calls)
                converted.append(openai_message)
            elif role == "user" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        converted.append(
                            {
                                "role": "tool",
                                "tool_call_id": block.get("tool_use_id"),
                                "content": str(block.get("content", "")),
                            }
                        )
            else:
                converted.append({"role": role or "user", "content": str(content)})
        return converted
