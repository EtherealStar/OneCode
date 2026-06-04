"""Tool executor protocol and registry-backed implementation."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
from typing import TYPE_CHECKING, Any, Protocol

from services.guard import GuardPolicy, SandboxGuard
from services.hooks import HookEvent, HookRegistry
from services.permissions import PermissionPolicy, PermissionPrompter
from services.permissions.types import PermissionDecision, PermissionResponse
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ValidationResult,
)

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState


@dataclass(frozen=True)
class _PreparedInputError:
    result: ToolExecutionResult
    guard_policies: tuple[GuardPolicy, ...] = ()


@dataclass(frozen=True)
class _PreparedInput:
    classification: ToolCallClassification
    guard_policies: tuple[GuardPolicy, ...] = ()
    approved_guard_policies: tuple[GuardPolicy, ...] = ()


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
        permission_policy: PermissionPolicy | None = None,
        permission_prompter: PermissionPrompter | None = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._hooks = hooks or HookRegistry()
        self._permission_policy = permission_policy
        self._permission_prompter = permission_prompter

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
        # hook 之前先校验、分类并执行 guard，确保 deny/ask 基于模型原始请求，
        # 不能被 hook 改写绕过。
        prepared = self._prepare_input(tool_call, descriptor, tool_input, runtime)
        if isinstance(prepared, _PreparedInputError):
            return self._tool_error(
                tool_call,
                descriptor,
                state,
                prepared.result,
                guard_policies=prepared.guard_policies,
            )
        runtime = replace(
            runtime,
            approved_guard_policies=prepared.approved_guard_policies,
        )
        classification = prepared.classification

        hook_result = self._hooks.run(
            HookEvent.PRE_TOOL_USE,
            {
                "tool_call": tool_call,
                "descriptor": descriptor,
                "tool_input": dict(tool_input),
                "classification": classification,
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
            # hook 修改后的输入视为一次新请求，必须重新通过 schema、工具校验、
            # 分类和 guard 检查。
            prepared = self._prepare_input(tool_call, descriptor, tool_input, runtime)
            if isinstance(prepared, _PreparedInputError):
                return self._tool_error(
                    tool_call,
                    descriptor,
                    state,
                    prepared.result,
                    guard_policies=prepared.guard_policies,
                )
            runtime = replace(
                runtime,
                approved_guard_policies=prepared.approved_guard_policies,
            )
            classification = prepared.classification

        try:
            result = descriptor.handler(tool_input, runtime)
            final_result = ToolExecutionResult(
                tool_call_id=tool_call.id,
                tool_name=descriptor.name,
                content=result.content,
                is_error=result.is_error,
                metadata=result.metadata,
            )
            if not final_result.is_error:
                final_result = self._apply_result_policy(
                    final_result,
                    classification.result_policy,
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
                    "classification": classification,
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

    def _prepare_input(
        self,
        tool_call: ToolCall,
        descriptor: ToolDescriptor,
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> _PreparedInput | _PreparedInputError:
        validation_result = self._validate_input(descriptor, tool_input, runtime)
        if validation_result is not None:
            return _PreparedInputError(validation_result)

        try:
            classification = descriptor.classify_input(tool_input, runtime)
        except Exception as exc:
            return _PreparedInputError(
                _error_result(tool_call, "tool_classification_error", str(exc))
            )

        try:
            guard_policies = self._check_guard(classification)
        except Exception as exc:
            return _PreparedInputError(
                _error_result(tool_call, "tool_guard_error", str(exc))
            )
        decision_result = self._evaluate_permission(
            tool_call=tool_call,
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
            tool_input=tool_input,
            runtime=runtime,
        )
        if isinstance(decision_result, _PreparedInputError):
            return decision_result
        approved_guard_policies = ()
        if decision_result.action == "allow":
            # If allow came from a session grant after guard returned ask, pass
            # those guard policies to handlers so their repeat guard checks agree.
            approved_guard_policies = guard_policies
        return _PreparedInput(
            classification=classification,
            guard_policies=guard_policies,
            approved_guard_policies=approved_guard_policies,
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
        classification: ToolCallClassification,
    ) -> tuple[GuardPolicy, ...]:
        policies: list[GuardPolicy] = []
        for target in classification.targets:
            if target.kind not in {"file", "directory"}:
                continue
            if self._guard is None:
                raise RuntimeError("Filesystem tool target requires a sandbox guard.")
            if target.operation not in {"read", "write", "list", "delete"}:
                raise RuntimeError(
                    f"Unsupported filesystem guard operation: {target.operation}"
                )
            # guard 消费抽象 target，而不是工具名；这样文件系统策略不会散落到
            # 主循环或具体工具里。
            policy = self._guard.check_path(
                target.value,
                operation=target.operation,
                kind=target.kind,
            )
            policies.append(policy)
        return tuple(policies)

    def _evaluate_permission(
        self,
        *,
        tool_call: ToolCall,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> PermissionDecision | _PreparedInputError:
        if self._permission_policy is None:
            return self._fallback_guard_decision(
                tool_call,
                classification,
                guard_policies,
            )

        decision = self._permission_policy.evaluate(
            tool_call=tool_call,
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
            state=runtime.state,
        )
        if decision.action == "deny":
            return _PreparedInputError(
                _permission_denied_result(tool_call, decision),
                guard_policies=guard_policies,
            )
        if decision.action != "ask":
            return decision

        request = self._permission_policy.request_for_decision(
            tool_call=tool_call,
            descriptor=descriptor,
            classification=classification,
            decision=decision,
            tool_input=tool_input,
        )
        if self._permission_prompter is None:
            return _PreparedInputError(
                _permission_ask_required_result(tool_call, decision),
                guard_policies=guard_policies,
            )

        try:
            response = self._permission_prompter.request_permission(request)
        except (EOFError, KeyboardInterrupt):
            response = PermissionResponse(
                action="deny",
                feedback="Permission prompt was interrupted.",
            )
        if response.action != "allow":
            return _PreparedInputError(
                _user_denied_result(tool_call, decision, response),
                guard_policies=guard_policies,
            )
        self._permission_policy.record_response(request, response)
        return PermissionDecision(
            action="allow",
            reason="User allowed the permission request.",
            source=f"user:{response.scope}",
            targets=decision.targets,
            guard_policies=guard_policies,
            metadata={**decision.metadata, "response_scope": response.scope},
        )

    def _fallback_guard_decision(
        self,
        tool_call: ToolCall,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
    ) -> PermissionDecision | _PreparedInputError:
        for policy in guard_policies:
            if policy.action == "deny":
                return _PreparedInputError(
                    _guard_error_result(tool_call, policy),
                    guard_policies=guard_policies,
                )
        for policy in guard_policies:
            if policy.action == "ask":
                decision = PermissionDecision(
                    action="ask",
                    reason=policy.reason,
                    source="guard",
                    guard_policies=guard_policies,
                    metadata={"guard_policy": policy.to_tool_error()},
                )
                return _PreparedInputError(
                    _permission_ask_required_result(tool_call, decision),
                    guard_policies=guard_policies,
                )
        for target in classification.targets:
            if (
                target.kind == "command"
                and target.operation == "execute"
                and not classification.read_only
            ):
                decision = PermissionDecision(
                    action="ask",
                    reason="Command may modify system state or has unknown side effects.",
                    source="permission_policy",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                )
                return _PreparedInputError(
                    _permission_ask_required_result(tool_call, decision),
                    guard_policies=guard_policies,
                )
        return PermissionDecision(
            action="allow",
            reason="Guard allowed the tool call.",
            source="guard",
            guard_policies=guard_policies,
        )

    def _apply_result_policy(
        self,
        result: ToolExecutionResult,
        policy: ToolResultPolicy,
    ) -> ToolExecutionResult:
        max_chars = policy.max_result_size_chars
        if max_chars is None or math.isinf(max_chars) or len(result.content) <= max_chars:
            return result

        # durable result store 尚未实现；当前先返回结构化预览，并保留截断
        # metadata，供后续 compaction/result-store 接入。
        preview = result.content[: policy.preview_chars]
        payload = {
            "result_truncated": True,
            "original_size_chars": len(result.content),
            "max_result_size_chars": max_chars,
            "preview": preview,
        }
        metadata = {
            **result.metadata,
            "result_truncated": True,
            "original_size_chars": len(result.content),
            "max_result_size_chars": max_chars,
        }
        return ToolExecutionResult(
            tool_call_id=result.tool_call_id,
            tool_name=result.tool_name,
            content=json.dumps(payload, ensure_ascii=False),
            is_error=result.is_error,
            metadata=metadata,
        )


    def _tool_error(
        self,
        tool_call: ToolCall,
        descriptor: ToolDescriptor | None,
        state: RuntimeState,
        result: ToolExecutionResult,
        *,
        guard_policies: tuple[GuardPolicy, ...] = (),
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
                "guard_policies": guard_policies,
                "guard_policy": guard_policies[0] if guard_policies else None,
            },
        )
        return final_result


def _validate_input_schema(
    tool_input: dict[str, Any],
    schema: dict[str, Any],
) -> ValidationResult:
    # 这里故意只实现很小的 JSON Schema 子集；共享校验只管形状，语义规则交给
    # 具体工具的 validator。
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


def _permission_denied_result(
    tool_call: ToolCall,
    decision: PermissionDecision,
) -> ToolExecutionResult:
    for policy in decision.guard_policies:
        if policy.action == "deny":
            return _guard_error_result(tool_call, policy)
    payload = {
        "error": "permission_denied",
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
        "reason": decision.reason,
        "decision": "deny",
        "source": decision.source,
    }
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": "permission_denied", "source": decision.source},
    )


def _permission_ask_required_result(
    tool_call: ToolCall,
    decision: PermissionDecision,
) -> ToolExecutionResult:
    payload = {
        "error": "permission_ask_required",
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
        "reason": decision.reason,
        "decision": "ask",
        "source": decision.source,
    }
    guard_payloads = [policy.to_tool_error() for policy in decision.guard_policies]
    if guard_payloads:
        payload["guard_policies"] = guard_payloads
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": "permission_ask_required", "source": decision.source},
    )


def _user_denied_result(
    tool_call: ToolCall,
    decision: PermissionDecision,
    response: PermissionResponse,
) -> ToolExecutionResult:
    reason = response.feedback or "User denied the permission request."
    payload = {
        "error": "permission_denied",
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
        "reason": reason,
        "requested_reason": decision.reason,
        "decision": "deny",
        "source": "user",
    }
    return ToolExecutionResult(
        tool_call_id=tool_call.id,
        tool_name=tool_call.name,
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": "permission_denied", "source": "user"},
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
