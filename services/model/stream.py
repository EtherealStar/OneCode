"""Provider-neutral model streaming events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from services.model.types import ModelUsage
from services.tools.types import ToolCall


ModelStreamEventType = Literal[
    "content_delta",
    "tool_call_delta",
    "tool_call_completed",
    "message_completed",
    "usage",
    "error",
]


@dataclass(frozen=True)
class ModelStreamEvent:
    type: ModelStreamEventType
    text: str = ""
    block_index: int | None = None
    tool_call: ToolCall | None = None
    assistant_message: dict[str, Any] | None = None
    final_text: str = ""
    stop_reason: str | None = None
    usage: ModelUsage | None = None
    output_interrupted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def content_delta(
        cls,
        text: str,
        *,
        block_index: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ModelStreamEvent":
        return cls(
            type="content_delta",
            text=text,
            block_index=block_index,
            metadata=metadata or {},
        )

    @classmethod
    def tool_call_delta(
        cls,
        *,
        metadata: dict[str, Any],
    ) -> "ModelStreamEvent":
        return cls(type="tool_call_delta", metadata=metadata)

    @classmethod
    def tool_call_completed(
        cls,
        tool_call: ToolCall,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> "ModelStreamEvent":
        return cls(
            type="tool_call_completed",
            tool_call=tool_call,
            metadata=metadata or {},
        )

    @classmethod
    def message_completed(
        cls,
        *,
        assistant_message: dict[str, Any],
        final_text: str,
        tool_calls: tuple[ToolCall, ...] = (),
        stop_reason: str | None = None,
        usage: ModelUsage | None = None,
        output_interrupted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> "ModelStreamEvent":
        return cls(
            type="message_completed",
            assistant_message=assistant_message,
            final_text=final_text,
            stop_reason=stop_reason,
            usage=usage,
            output_interrupted=output_interrupted,
            metadata={
                **(metadata or {}),
                "tool_calls": tool_calls,
            },
        )

    @classmethod
    def usage_event(cls, usage: ModelUsage) -> "ModelStreamEvent":
        return cls(type="usage", usage=usage)
