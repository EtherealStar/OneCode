"""Shared tool call and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class ToolTarget:
    kind: str
    operation: str
    value: str
    normalized_value: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultPolicy:
    max_result_size_chars: int | float = 50_000
    persist_when_exceeded: bool = True
    preview_chars: int = 4_000


@dataclass(frozen=True)
class ToolCallClassification:
    """单次工具调用的输入感知执行元数据。

    默认值刻意保守，避免分类失败时意外授予只读或可并发行为。
    """

    read_only: bool = False
    modifies_filesystem: bool = True
    concurrency_safe: bool = False
    targets: tuple[ToolTarget, ...] = field(default_factory=tuple)
    result_policy: ToolResultPolicy = field(default_factory=ToolResultPolicy)
    permission_subject: str = ""


ToolHandler = Callable[[dict[str, Any], ToolRuntime], ToolExecutionResult]
ToolValidator = Callable[[dict[str, Any], ToolRuntime], ValidationResult]
ToolClassifier = Callable[[dict[str, Any], ToolRuntime], ToolCallClassification]


def default_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "is_error": {"type": "boolean"},
            "metadata": {"type": "object"},
            "data": {"type": "object"},
        },
        "required": ["content", "is_error"],
        "additionalProperties": False,
    }


def fail_closed_classification(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification()


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    output_schema: dict[str, Any] = field(default_factory=default_output_schema)
    prompt: str = ""
    search_hint: str = ""
    validate_input: ToolValidator | None = None
    classify_input: ToolClassifier = fail_closed_classification
