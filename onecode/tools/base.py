from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolMeta:
    name: str
    description: str
    input_schema: dict
    read_only: bool
    concurrency_safe: bool
    mutates_filesystem: bool = False
    requires_permission: bool = False
    max_result_chars: int | None = 50_000
    timeout_seconds: int = 120

    def api_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(frozen=True)
class Tool:
    meta: ToolMeta
    handler: Callable[..., str]


@dataclass
class ToolExecutionResult:
    tool_use_id: str
    content: str
    is_error: bool = False

    def to_message_block(self) -> dict:
        block = {
            "type": "tool_result",
            "tool_use_id": self.tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            block["is_error"] = True
        return block
