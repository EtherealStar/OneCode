"""Runtime event types emitted by the async agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from services.tools.types import ToolExecutionResult


AgentEventType = Literal[
    "interaction_started",
    "assistant_delta",
    "assistant_message_completed",
    "tool_call_ready",
    "tool_started",
    "tool_progress",
    "tool_result",
    "transition",
    "completed",
    "error",
]


@dataclass(frozen=True)
class AgentEvent:
    type: AgentEventType
    text: str = ""
    result: ToolExecutionResult | None = None
    transition: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
