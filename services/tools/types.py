"""Shared tool call and result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState
    from services.guard import GuardPolicy
    from services.guard import SandboxGuard
    from services.tools.file_state import FileStateCache


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
    followup_messages: tuple[dict[str, Any], ...] = field(default_factory=tuple)


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
    file_state_cache: FileStateCache | None = None
    approved_guard_policies: tuple[GuardPolicy, ...] = ()
    tool_call_id: str = ""


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


def is_guard_policy_allowed(policy: GuardPolicy, runtime: ToolRuntime) -> bool:
    """Return whether a handler may proceed after its own guard check.

    Handlers keep their local guard checks as a final safety net. This helper
    lets the executor's permission approval cover matching ``ask`` policies
    without allowing a deny result to be bypassed.
    """

    if policy.action == "allow":
        return True
    if policy.action == "deny":
        return False
    for approved in runtime.approved_guard_policies:
        if approved.action == "deny":
            continue
        if _same_guard_target(policy, approved) or _approved_directory_contains(
            policy,
            approved,
        ):
            return True
    return False


def _same_guard_target(policy: GuardPolicy, approved: GuardPolicy) -> bool:
    return (
        policy.operation == approved.operation
        and policy.target_kind == approved.target_kind
        and policy.normalized_path == approved.normalized_path
    )


def _approved_directory_contains(policy: GuardPolicy, approved: GuardPolicy) -> bool:
    if approved.target_kind != "directory":
        return False
    if policy.operation not in {"read", "list"} or approved.operation not in {
        "read",
        "list",
    }:
        return False
    try:
        policy.normalized_path.relative_to(approved.normalized_path)
    except ValueError:
        return False
    return True
