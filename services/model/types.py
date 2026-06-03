"""Provider-neutral model response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.tools.types import ToolCall


class ProviderError(Exception):
    """Provider-neutral model error raised by infrastructure adapters."""

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        status_code: int | None = None,
        error_type: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider_id = provider_id
        self.status_code = status_code
        self.error_type = error_type
        self.retryable = retryable


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, other: "ModelUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_input_tokens += other.cache_read_input_tokens
        self.cache_creation_input_tokens += other.cache_creation_input_tokens


@dataclass(frozen=True)
class LLMResponse:
    assistant_message: dict[str, Any]
    final_text: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    stop_reason: str | None = None
    usage: ModelUsage | None = None
    output_interrupted: bool = False
