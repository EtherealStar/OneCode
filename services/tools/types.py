"""Shared tool call and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState
    from services.guard import SandboxGuard


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecutionResult:
    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str | None = None

    @classmethod
    def success(cls) -> "ValidationResult":
        return cls(ok=True)

    @classmethod
    def failure(cls, message: str) -> "ValidationResult":
        return cls(ok=False, message=message)


@dataclass(frozen=True)
class ToolRuntime:
    state: RuntimeState
    guard: SandboxGuard | None = None


ToolHandler = Callable[[dict[str, Any], ToolRuntime], ToolExecutionResult]
ToolValidator = Callable[[dict[str, Any], ToolRuntime], ValidationResult]
ToolPathGetter = Callable[[dict[str, Any]], str | Path | None]


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    read_only: bool = False
    modifies_filesystem: bool = False
    requires_guard: bool = False
    concurrency_safe: bool = False
    max_result_size_chars: int | None = None
    prompt: str = ""
    validate_input: ToolValidator | None = None
    get_path: ToolPathGetter | None = None
