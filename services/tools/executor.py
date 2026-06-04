"""Tool executor protocol and registry-backed implementation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol

from services.guard import GuardPolicy, SandboxGuard
from services.hooks import HookEvent, HookRegistry
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ValidationResult,
)

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState


class ToolExecutor(Protocol):
    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: object,
    ) -> list[ToolExecutionResult]:
        ...


class RegistryToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        guard: SandboxGuard | None = None,
        hooks: HookRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._hooks = hooks or HookRegistry()

    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ) -> list[ToolExecutionResult]:
        return [self._execute_one(tool_call, state) for tool_call in tool_calls]

    def _execute_one(
        self,
        tool_call: ToolCall,
        state: RuntimeState,
    ) -> ToolExecutionResult:
        descriptor = self._registry.get(tool_call.name)
        if descriptor is None:
            return self._tool_error(
                tool_call,
                None,
                state,
                _error_result(
                    tool_call,
                    "unknown_tool",
                    f"Tool is not registered: {tool_call.name}",
                ),
            )

        runtime = ToolRuntime(state=state, guard=self._guard)
        tool_input = dict(tool_call.input)
        validation_result = self._validate_input(descriptor, tool_input, runtime)
        if validation_result is not None:
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                validation_result,
            )

        try:
            guard_policy = self._check_guard(descriptor, tool_input)
        except Exception as exc:
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                _error_result(tool_call, "tool_guard_error", str(exc)),
            )
        if guard_policy is not None and guard_policy.action != "allow":
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                _guard_error_result(tool_call, guard_policy),
                guard_policy=guard_policy,
            )

        hook_result = self._hooks.run(
            HookEvent.PRE_TOOL_USE,
            {
                "tool_call": tool_call,
                "descriptor": descriptor,
                "tool_input": dict(tool_input),
                "state": state,
            },
        )
        if hook_result.blocking_error is not None:
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                _error_result(
                    tool_call,
                    "hook_blocked",
                    hook_result.blocking_error,
                ),
            )
        if hook_result.updated_input is not None:
            tool_input = dict(hook_result.updated_input)
            validation_result = self._validate_input(descriptor, tool_input, runtime)
            if validation_result is not None:
                return self._tool_error(
                    tool_call,
                    descriptor,
                    state,
                    validation_result,
                )
            try:
                guard_policy = self._check_guard(descriptor, tool_input)
            except Exception as exc:
                return self._tool_error(
                    tool_call,
                    descriptor,
                    state,
                    _error_result(tool_call, "tool_guard_error", str(exc)),
                )
            if guard_policy is not None and guard_policy.action != "allow":
                return self._tool_error(
                    tool_call,
                    descriptor,
                    state,
                    _guard_error_result(tool_call, guard_policy),
                    guard_policy=guard_policy,
                )

        try:
            result = descriptor.handler(tool_input, runtime)
            final_result = ToolExecutionResult(
                tool_call_id=tool_call.id,
                tool_name=descriptor.name,
                content=result.content,
                is_error=result.is_error,
                metadata=result.metadata,
            )
            if final_result.is_error:
                return self._tool_error(
                    tool_call,
                    descriptor,
                    state,
                    final_result,
                )
            self._hooks.run(
                HookEvent.POST_TOOL_USE,
                {
                    "tool_call": tool_call,
                    "descriptor": descriptor,
                    "tool_input": dict(tool_input),
                    "state": state,
                    "result": final_result,
                },
            )
            return final_result
        except Exception as exc:
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                _error_result(tool_call, "tool_execution_error", str(exc)),
            )

    def _validate_input(
        self,
        descriptor: ToolDescriptor,
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult | None:
        validation = _validate_input_schema(tool_input, descriptor.input_schema)
        if not validation.ok:
            return _error_result(
                ToolCall(id="", name=descriptor.name, input=tool_input),
                "invalid_tool_input",
                validation.message or "Tool input is invalid.",
            )
        if descriptor.validate_input is None:
            return None
        try:
            validation = descriptor.validate_input(tool_input, runtime)
        except Exception as exc:
            return _error_result(
                ToolCall(id="", name=descriptor.name, input=tool_input),
                "tool_validation_error",
                str(exc),
            )
        if not validation.ok:
            return _error_result(
                ToolCall(id="", name=descriptor.name, input=tool_input),
                "invalid_tool_input",
                validation.message or "Tool input is invalid.",
            )
        return None

    def _check_guard(
        self,
        descriptor: ToolDescriptor,
        tool_input: dict[str, Any],
    ) -> GuardPolicy | None:
        if not descriptor.requires_guard or descriptor.get_path is None:
            return None
        if self._guard is None:
            raise RuntimeError(f"Tool requires guard but no guard is configured: {descriptor.name}")
        target = descriptor.get_path(tool_input)
        if target is None:
            return None
        if descriptor.modifies_filesystem:
            return self._guard.check_write_target(target)
        return self._guard.check_path(target, operation="read")

    def _tool_error(
        self,
        tool_call: ToolCall,
        descriptor: ToolDescriptor | None,
        state: RuntimeState,
        result: ToolExecutionResult,
        *,
        guard_policy: GuardPolicy | None = None,
    ) -> ToolExecutionResult:
        final_result = ToolExecutionResult(
            tool_call_id=tool_call.id,
            tool_name=descriptor.name if descriptor is not None else tool_call.name,
            content=result.content,
            is_error=True,
            metadata=result.metadata,
        )
        self._hooks.run(
            HookEvent.TOOL_ERROR,
            {
                "tool_call": tool_call,
                "descriptor": descriptor,
                "tool_input": dict(tool_call.input),
                "state": state,
                "result": final_result,
                "guard_policy": guard_policy,
            },
        )
        return final_result


def _validate_input_schema(
    tool_input: dict[str, Any],
    schema: dict[str, Any],
) -> ValidationResult:
    if schema.get("type") != "object":
        return ValidationResult.success()
    if not isinstance(tool_input, dict):
        return ValidationResult.failure("Tool input must be a JSON object.")

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}

    required = schema.get("required", [])
    if not isinstance(required, list):
        required = []
    for name in required:
        if isinstance(name, str) and name not in tool_input:
            return ValidationResult.failure(f"Missing required input field: {name}")

    if schema.get("additionalProperties") is False:
        allowed = set(properties.keys())
        for name in tool_input:
            if name not in allowed:
                return ValidationResult.failure(f"Unexpected input field: {name}")

    for name, value in tool_input.items():
        property_schema = properties.get(name)
        if isinstance(property_schema, dict):
            validation = _validate_property(name, value, property_schema)
            if not validation.ok:
                return validation

    return ValidationResult.success()


def _validate_property(
    name: str,
    value: Any,
    schema: dict[str, Any],
) -> ValidationResult:
    expected_type = schema.get("type")
    if expected_type == "string" and not isinstance(value, str):
        return ValidationResult.failure(f"Input field must be a string: {name}")
    if expected_type == "boolean" and not isinstance(value, bool):
        return ValidationResult.failure(f"Input field must be a boolean: {name}")
    if expected_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return ValidationResult.failure(f"Input field must be an integer: {name}")
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return ValidationResult.failure(
                f"Input field must be greater than or equal to {minimum}: {name}"
            )
    return ValidationResult.success()


def _guard_error_result(
    tool_call: ToolCall,
    policy: GuardPolicy,
) -> ToolExecutionResult:
    payload = policy.to_tool_error()
    if policy.action == "ask":
        payload["error"] = "path_guard_ask_required"
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": payload["error"]},
    )


def _error_result(
    tool_call: ToolCall,
    error: str,
    message: str,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=json.dumps(
            {
                "error": error,
                "message": message,
            },
            ensure_ascii=False,
        ),
        is_error=True,
        metadata={"error": error},
    )
