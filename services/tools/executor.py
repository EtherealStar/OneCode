"""Tool executor protocol and registry-backed implementation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
import inspect
import json
import math
import os
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal, Protocol
from pathlib import Path

from services.guard import GuardPolicy, SandboxGuard
from services.hooks import HookEvent, HookRegistry
from services.compaction.result_store import ToolResultStore
from services.observability import TraceRecorder
from services.permissions import PermissionPolicy, PermissionPrompter
from services.permissions.types import PermissionDecision, PermissionResponse
from services.tools.file_state import FileStateCache
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


DEFAULT_MAX_TOOL_CONCURRENCY = 10
FILE_STATE_TOOL_NAMES = {"read_file", "edit_file", "write_file", "filewrite"}


@dataclass(frozen=True)
class _PreparedInputError:
    result: ToolExecutionResult
    guard_policies: tuple[GuardPolicy, ...] = ()
    permission_decision: PermissionDecision | None = None


@dataclass(frozen=True)
class _PreparedInput:
    classification: ToolCallClassification
    guard_policies: tuple[GuardPolicy, ...] = ()
    approved_guard_policies: tuple[GuardPolicy, ...] = ()
    permission_decision: PermissionDecision | None = None


@dataclass(frozen=True)
class _ReadyToolCall:
    tool_call: ToolCall
    descriptor: ToolDescriptor
    tool_input: dict[str, Any]
    runtime: ToolRuntime
    classification: ToolCallClassification
    guard_policies: tuple[GuardPolicy, ...] = ()
    trace_parent_span_id: str | None = None


@dataclass(frozen=True)
class _HandlerOutcome:
    ready: _ReadyToolCall
    result: ToolExecutionResult | None = None
    exception: Exception | None = None


@dataclass(frozen=True)
class ToolExecutionUpdate:
    type: Literal["started", "progress", "result", "error"]
    result: ToolExecutionResult | None = None
    tool_call_id: str = ""
    tool_name: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolExecutor(Protocol):
    def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: object,
    ) -> AsyncIterator[ToolExecutionUpdate]:
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
        max_tool_concurrency: int | None = None,
        trace_recorder: TraceRecorder | None = None,
        result_store: ToolResultStore | None = None,
        file_state_cache: FileStateCache | None = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._hooks = hooks or HookRegistry()
        self._permission_policy = permission_policy
        self._permission_prompter = permission_prompter
        self._max_tool_concurrency = _resolve_max_tool_concurrency(
            max_tool_concurrency
        )
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._result_store = result_store
        self._file_state_cache = file_state_cache or FileStateCache()

    def bind_result_store(self, result_store: ToolResultStore | None) -> None:
        self._result_store = result_store

    def bind_file_state_cache(self, file_state_cache: FileStateCache | None) -> None:
        self._file_state_cache = file_state_cache or FileStateCache()

    @property
    def file_state_cache(self) -> FileStateCache:
        return self._file_state_cache

    async def execute(
        self,
        tool_calls: tuple[ToolCall, ...],
        state: RuntimeState,
    ) -> AsyncIterator[ToolExecutionUpdate]:
        with self._trace_recorder.span(
            "tool_batch",
            {
                "tool_call_count": len(tool_calls),
                "concurrency_candidate_count": sum(
                    1 for call in tool_calls if self._is_concurrency_candidate(call, state)
                ),
            },
        ) as batch_span:
            index = 0
            while index < len(tool_calls):
                tool_call = tool_calls[index]
                if not self._is_concurrency_candidate(tool_call, state):
                    async for update in self._execute_one(
                            tool_call,
                            state,
                            parent_span_id=batch_span.span_id,
                    ):
                        yield update
                    index += 1
                    continue

                batch: list[ToolCall] = []
                while index < len(tool_calls) and self._is_concurrency_candidate(
                    tool_calls[index],
                    state,
                ):
                    batch.append(tool_calls[index])
                    index += 1
                async for update in self._execute_concurrency_candidate_batch(
                        batch,
                        state,
                        parent_span_id=batch_span.span_id,
                ):
                    yield update

    async def _execute_one(
        self,
        tool_call: ToolCall,
        state: RuntimeState,
        *,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[ToolExecutionUpdate]:
        ready = await self._preflight_one(tool_call, state, parent_span_id=parent_span_id)
        if isinstance(ready, ToolExecutionResult):
            self._record_tool_result(ready, parent_span_id=parent_span_id)
            yield _result_update(ready, update_type="error" if ready.is_error else "result")
            return
        yield _started_update(ready)
        final = await self._finalize_outcome(
            await self._run_handler_async(ready),
            state,
        )
        yield _result_update(final, update_type="error" if final.is_error else "result")

    async def _preflight_one(
        self,
        tool_call: ToolCall,
        state: RuntimeState,
        *,
        parent_span_id: str | None = None,
    ) -> _ReadyToolCall | ToolExecutionResult:
        """Run all handler-before checks serially for one tool call."""
        with self._trace_recorder.span(
            "tool_preflight",
            {
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
            },
            parent_span_id=parent_span_id,
        ) as span:
            descriptor = self._registry.get(tool_call.name)
            if descriptor is None:
                result = await self._tool_error(
                    tool_call,
                    None,
                    state,
                    _error_result(
                        tool_call,
                        "unknown_tool",
                        f"Tool is not registered: {tool_call.name}",
                    ),
                )
                span.end({"permission_action": "unknown_tool"})
                return result

            runtime = ToolRuntime(
                state=state,
                guard=self._guard,
                file_state_cache=self._file_state_cache,
                tool_call_id=tool_call.id,
            )
            tool_input = dict(tool_call.input)
            # hook 之前先校验、分类并执行 guard，确保 deny/ask 基于模型原始请求，
            # 不能被 hook 改写绕过。
            prepared = await self._prepare_input(tool_call, descriptor, tool_input, runtime)
            if isinstance(prepared, _PreparedInputError):
                span.end(
                    self._preflight_trace_attributes(
                        descriptor,
                        None,
                        prepared.guard_policies,
                        prepared.permission_decision,
                    )
                )
                return await self._tool_error(
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

            hook_result = await self._hooks.run(
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
                span.end(
                    {
                        **self._preflight_trace_attributes(
                            descriptor,
                            classification,
                            prepared.guard_policies,
                            prepared.permission_decision,
                        ),
                        "permission_action": "hook_blocked",
                    }
                )
                return await self._tool_error(
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
                prepared = await self._prepare_input(tool_call, descriptor, tool_input, runtime)
                if isinstance(prepared, _PreparedInputError):
                    span.end(
                        self._preflight_trace_attributes(
                            descriptor,
                            None,
                            prepared.guard_policies,
                            prepared.permission_decision,
                        )
                    )
                    return await self._tool_error(
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

            span.end(
                self._preflight_trace_attributes(
                    descriptor,
                    classification,
                    prepared.guard_policies,
                    prepared.permission_decision,
                )
            )
            return _ReadyToolCall(
                tool_call=tool_call,
                descriptor=descriptor,
                tool_input=tool_input,
                runtime=runtime,
                classification=classification,
                guard_policies=prepared.guard_policies,
                trace_parent_span_id=parent_span_id,
            )

    async def _run_handler_async(self, ready: _ReadyToolCall) -> _HandlerOutcome:
        if inspect.iscoroutinefunction(ready.descriptor.handler):
            with self._trace_recorder.span(
                "tool_execution",
                {
                    "tool_name": ready.descriptor.name,
                    "tool_call_id": ready.tool_call.id,
                },
                parent_span_id=ready.trace_parent_span_id,
            ):
                try:
                    result = await ready.descriptor.handler(
                        ready.tool_input,
                        ready.runtime,
                    )
                    return _HandlerOutcome(ready=ready, result=result)
                except Exception as exc:
                    return _HandlerOutcome(ready=ready, exception=exc)
        return await asyncio.to_thread(self._run_handler, ready)

    def _run_handler(self, ready: _ReadyToolCall) -> _HandlerOutcome:
        """Execute only the concrete handler so it can safely run in a worker."""
        with self._trace_recorder.span(
            "tool_execution",
            {
                "tool_name": ready.descriptor.name,
                "tool_call_id": ready.tool_call.id,
            },
            parent_span_id=ready.trace_parent_span_id,
        ):
            try:
                return _HandlerOutcome(
                    ready=ready,
                    result=ready.descriptor.handler(ready.tool_input, ready.runtime),
                )
            except Exception as exc:
                return _HandlerOutcome(ready=ready, exception=exc)

    async def _finalize_outcome(
        self,
        outcome: _HandlerOutcome,
        state: RuntimeState,
    ) -> ToolExecutionResult:
        """Normalize a handler outcome and apply hooks, budgets, and state effects."""
        ready = outcome.ready
        if outcome.exception is not None:
            result = await self._tool_error(
                ready.tool_call,
                ready.descriptor,
                state,
                _error_result(
                    ready.tool_call,
                    "tool_execution_error",
                    str(outcome.exception),
                ),
                guard_policies=ready.guard_policies,
            )
            self._record_tool_result(result, parent_span_id=ready.trace_parent_span_id)
            return result

        assert outcome.result is not None
        final_result = ToolExecutionResult(
            tool_call_id=ready.tool_call.id,
            tool_name=ready.descriptor.name,
            content=outcome.result.content,
            is_error=outcome.result.is_error,
            metadata=outcome.result.metadata,
            followup_messages=outcome.result.followup_messages,
        )
        if not final_result.is_error:
            final_result = self._apply_result_policy(
                final_result,
                ready.classification.result_policy,
            )
        if final_result.is_error:
            result = await self._tool_error(
                ready.tool_call,
                ready.descriptor,
                state,
                final_result,
                guard_policies=ready.guard_policies,
            )
            self._record_tool_result(result, parent_span_id=ready.trace_parent_span_id)
            return result
        await self._hooks.run(
            HookEvent.POST_TOOL_USE,
            {
                "tool_call": ready.tool_call,
                "descriptor": ready.descriptor,
                "tool_input": dict(ready.tool_input),
                "classification": ready.classification,
                "state": state,
                "result": final_result,
            },
        )
        self._apply_success_side_effects(
            final_result,
            state,
            tool_input=ready.tool_input,
        )
        self._record_tool_result(
            final_result,
            parent_span_id=ready.trace_parent_span_id,
        )
        return final_result

    def _is_concurrency_candidate(
        self,
        tool_call: ToolCall,
        state: RuntimeState,
    ) -> bool:
        """Conservatively classify raw input for initial batch planning."""
        descriptor = self._registry.get(tool_call.name)
        if descriptor is None:
            return False
        runtime = ToolRuntime(
            state=state,
            guard=self._guard,
            file_state_cache=self._file_state_cache,
        )
        tool_input = dict(tool_call.input)
        if self._validate_input(descriptor, tool_input, runtime) is not None:
            return False
        try:
            classification = descriptor.classify_input(tool_input, runtime)
        except Exception:
            return False
        return classification.concurrency_safe

    async def _execute_concurrency_candidate_batch(
        self,
        tool_calls: list[ToolCall],
        state: RuntimeState,
        *,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[ToolExecutionUpdate]:
        """Preflight a safe-looking run, then execute allowed handlers in parallel."""
        prepared: list[_ReadyToolCall | ToolExecutionResult] = [
            await self._preflight_one(tool_call, state, parent_span_id=parent_span_id)
            for tool_call in tool_calls
        ]

        if any(
            isinstance(item, _ReadyToolCall)
            and not item.classification.concurrency_safe
            for item in prepared
        ):
            for item in prepared:
                if isinstance(item, ToolExecutionResult):
                    self._record_tool_result(item, parent_span_id=parent_span_id)
                    yield _result_update(
                        item,
                        update_type="error" if item.is_error else "result",
                    )
                    continue
                yield _started_update(item)
                result = await self._finalize_outcome(
                    await self._run_handler_async(item),
                    state,
                )
                yield _result_update(
                    result,
                    update_type="error" if result.is_error else "result",
                )
            return

        ready_calls = [
            item for item in prepared if isinstance(item, _ReadyToolCall)
        ]
        for item in ready_calls:
            yield _started_update(item)
        outcomes_by_id = {
            id(outcome.ready): outcome
            for outcome in await self._run_handlers_concurrently(ready_calls)
        }

        for item in prepared:
            if isinstance(item, ToolExecutionResult):
                self._record_tool_result(item, parent_span_id=parent_span_id)
                yield _result_update(
                    item,
                    update_type="error" if item.is_error else "result",
                )
                continue
            result = await self._finalize_outcome(outcomes_by_id[id(item)], state)
            yield _result_update(
                result,
                update_type="error" if result.is_error else "result",
            )

    async def _run_handlers_concurrently(
        self,
        ready_calls: list[_ReadyToolCall],
    ) -> list[_HandlerOutcome]:
        if len(ready_calls) <= 1:
            return [await self._run_handler_async(ready) for ready in ready_calls]
        worker_count = min(self._max_tool_concurrency, len(ready_calls))
        semaphore = asyncio.Semaphore(worker_count)

        async def run_one(ready: _ReadyToolCall) -> _HandlerOutcome:
            async with semaphore:
                return await self._run_handler_async(ready)

        return list(await asyncio.gather(*(run_one(ready) for ready in ready_calls)))

    async def _prepare_input(
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
        decision_result = await self._evaluate_permission(
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
            permission_decision=decision_result,
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

    async def _evaluate_permission(
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
                permission_decision=decision,
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
                permission_decision=decision,
            )

        try:
            with self._trace_recorder.span(
                "permission_wait",
                {
                    "tool_name": descriptor.name,
                    "tool_call_id": tool_call.id,
                    "permission_action": decision.action,
                },
            ) as span:
                response = await self._permission_prompter.request_permission(request)
                span.end(
                    {
                        "permission_action": response.action,
                        "permission_scope": response.scope,
                        "interrupted": False,
                    }
                )
        except (EOFError, KeyboardInterrupt):
            response = PermissionResponse(
                action="deny",
                feedback="Permission prompt was interrupted.",
            )
            self._trace_recorder.event(
                "permission_wait",
                {
                    "tool_name": descriptor.name,
                    "tool_call_id": tool_call.id,
                    "permission_action": response.action,
                    "interrupted": True,
                },
            )
        if response.action != "allow":
            return _PreparedInputError(
                _user_denied_result(tool_call, decision, response),
                guard_policies=guard_policies,
                permission_decision=decision,
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
                    permission_decision=PermissionDecision(
                        action="deny",
                        reason=policy.reason,
                        source="guard",
                        guard_policies=guard_policies,
                    ),
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
                    permission_decision=decision,
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
                    permission_decision=decision,
                )
        return PermissionDecision(
            action="allow",
            reason="Guard allowed the tool call.",
            source="guard",
            guard_policies=guard_policies,
        )

    def _preflight_trace_attributes(
        self,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification | None,
        guard_policies: tuple[GuardPolicy, ...],
        decision: PermissionDecision | None,
    ) -> dict[str, object]:
        attributes: dict[str, object] = {
            "tool_name": descriptor.name,
            "target_count": len(classification.targets) if classification else 0,
            "guard_actions": [policy.action for policy in guard_policies],
            "permission_action": decision.action if decision is not None else "allow",
        }
        if classification is not None:
            attributes.update(
                {
                    "read_only": classification.read_only,
                    "modifies_filesystem": classification.modifies_filesystem,
                    "concurrency_safe": classification.concurrency_safe,
                }
            )
        return attributes

    def _record_tool_result(
        self,
        result: ToolExecutionResult,
        *,
        parent_span_id: str | None,
    ) -> None:
        self._trace_recorder.event(
            "tool_result",
            {
                "tool_name": result.tool_name,
                "tool_call_id": result.tool_call_id,
                "is_error": result.is_error,
                "error": result.metadata.get("error"),
                "content_chars": len(result.content),
                "result_truncated": result.metadata.get("result_truncated") is True,
                "result_stored": result.metadata.get("result_stored") is True,
            },
            parent_span_id=parent_span_id,
        )

    def _apply_result_policy(
        self,
        result: ToolExecutionResult,
        policy: ToolResultPolicy,
    ) -> ToolExecutionResult:
        max_chars = policy.max_result_size_chars
        if max_chars is None or math.isinf(max_chars) or len(result.content) <= max_chars:
            return result

        preview = result.content[: policy.preview_chars]
        if policy.persist_when_exceeded and self._result_store is not None:
            stored_ref = self._result_store.persist_tool_result(
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                content=result.content,
            )
            metadata = {
                **result.metadata,
                "result_truncated": True,
                "result_stored": True,
                "original_size_chars": len(result.content),
                "max_result_size_chars": max_chars,
                "stored_result_id": stored_ref.result_id,
                "stored_result_path": str(stored_ref.absolute_path),
                "stored_result_relative_path": stored_ref.relative_path,
            }
            return ToolExecutionResult(
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                content=self._result_store.format_model_reference(
                    stored_ref,
                    preview=preview,
                ),
                is_error=result.is_error,
                metadata=metadata,
                followup_messages=result.followup_messages,
            )

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
            followup_messages=result.followup_messages,
        )

    def _apply_success_side_effects(
        self,
        result: ToolExecutionResult,
        state: RuntimeState,
        *,
        tool_input: dict[str, Any],
    ) -> None:
        """Apply executor-owned session state updates after successful results."""
        allowed_tools = result.metadata.get("allowed_tools")
        if (
            result.tool_name == "skill"
            and self._permission_policy is not None
            and allowed_tools
        ):
            for tool_name in _tool_names(allowed_tools):
                self._permission_policy.session_store.allow_tool(tool_name)

        if result.tool_name not in FILE_STATE_TOOL_NAMES:
            return
        path = result.metadata.get("path")
        if not isinstance(path, str) or not path:
            return

        # files_read is consumed by edit_file to enforce "read before edit".
        # The executor updates it serially so read handlers can remain parallel.
        files_read = state.metadata.setdefault("files_read", set())
        if not isinstance(files_read, set):
            files_read = set(files_read)
            state.metadata["files_read"] = files_read
        files_read.add(path)
        if result.tool_name in {"edit_file", "write_file", "filewrite"}:
            files_changed = state.metadata.setdefault("files_changed", set())
            if not isinstance(files_changed, set):
                files_changed = set(files_changed)
                state.metadata["files_changed"] = files_changed
            files_changed.add(path)
            if _is_long_term_memory_markdown_path(path):
                writes = state.metadata.setdefault("long_term_memory_writes", [])
                if not isinstance(writes, list):
                    writes = list(writes)
                    state.metadata["long_term_memory_writes"] = writes
                writes.append(
                    {
                        "turn_count": state.turn_count,
                        "path": path,
                    }
                )

        # The mtime cache lives in the tool service because file tools are the
        # durable source of observed file content, not attachment collection.
        self._file_state_cache.snapshot_path(
            Path(path),
            offset=_int_or_none(tool_input.get("offset")),
            limit=_int_or_none(tool_input.get("limit")),
            partial=result.tool_name == "read_file"
            and ("offset" in tool_input or "limit" in tool_input),
        )


    async def _tool_error(
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
            followup_messages=(),
        )
        await self._hooks.run(
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


def _started_update(ready: _ReadyToolCall) -> ToolExecutionUpdate:
    return ToolExecutionUpdate(
        type="started",
        tool_call_id=ready.tool_call.id,
        tool_name=ready.descriptor.name,
    )


def _result_update(
    result: ToolExecutionResult,
    *,
    update_type: Literal["result", "error"],
) -> ToolExecutionUpdate:
    return ToolExecutionUpdate(
        type=update_type,
        result=result,
        tool_call_id=result.tool_call_id,
        tool_name=result.tool_name,
    )


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


def _resolve_max_tool_concurrency(value: int | None = None) -> int:
    """Resolve the handler worker limit from constructor input or environment."""
    if value is not None:
        return value if value >= 1 else DEFAULT_MAX_TOOL_CONCURRENCY

    raw_value = os.environ.get("ONECODE_MAX_TOOL_CONCURRENCY")
    if raw_value is None or raw_value.strip() == "":
        return DEFAULT_MAX_TOOL_CONCURRENCY
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_MAX_TOOL_CONCURRENCY
    return parsed if parsed >= 1 else DEFAULT_MAX_TOOL_CONCURRENCY


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _tool_names(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_values = value.split(",")
    else:
        try:
            raw_values = tuple(value)  # type: ignore[arg-type]
        except TypeError:
            raw_values = (value,)
    names: list[str] = []
    for item in raw_values:
        name = str(item).strip()
        if name and name not in names:
            names.append(name)
    return tuple(names)


def _is_long_term_memory_markdown_path(path: str) -> bool:
    target = Path(path)
    parts = [part.lower() for part in target.parts]
    for index, part in enumerate(parts):
        if part != ".onecode":
            continue
        if index + 1 < len(parts) and parts[index + 1] == "memory":
            return target.suffix.lower() == ".md"
    return False
