"""Provider-neutral subagent request and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from services.model.types import ModelUsage

SubagentRunMode = Literal["clean", "fork"]


@dataclass(frozen=True)
class AgentDefinition:
    agent_type: str
    when_to_use: str
    system_prompt: str
    source: Literal["built-in"] = "built-in"
    tools: tuple[str, ...] = ("*",)
    disallowed_tools: tuple[str, ...] = ()
    max_turns: int | None = None
    model: str | None = None
    read_only: bool = False
    hidden: bool = False


@dataclass(frozen=True)
class SubagentRequest:
    prompt: str
    subagent_type: str | None
    parent_session_id: str
    parent_tool_call_id: str
    mode: SubagentRunMode | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubagentResult:
    agent_type: str
    session_id: str
    final_text: str
    is_error: bool = False
    transition: str | None = None
    usage: ModelUsage | None = None
    tool_result_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
