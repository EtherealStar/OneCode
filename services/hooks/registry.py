"""Ordered hook callback registry."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from services.hooks.events import HookEvent
from services.observability import TraceRecorder

HookPayload = dict[str, Any]


@dataclass(frozen=True)
class HookResult:
    blocking_error: str | None = None
    updated_input: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[[HookPayload], HookResult | Awaitable[HookResult | None] | None]


class HookRegistry:
    def __init__(self, trace_recorder: TraceRecorder | None = None) -> None:
        self._callbacks: dict[HookEvent, list[HookCallback]] = {
            event: [] for event in HookEvent
        }
        self._trace_recorder = trace_recorder or TraceRecorder.noop()

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._callbacks[event].append(callback)

    async def run(self, event: HookEvent, payload: HookPayload) -> HookResult:
        trace_attributes = {
            "hook_event": event.value,
            "callback_count": len(self._callbacks[event]),
            "tool_name": _payload_tool_name(payload),
            "tool_call_id": _payload_tool_call_id(payload),
        }
        with self._trace_recorder.span(
            "hook",
            trace_attributes,
        ) as span:
            merged_input: dict[str, Any] | None = None
            metadata: dict[str, Any] = {}
            blocking = False
            for callback in self._callbacks[event]:
                try:
                    result = callback(payload)
                    if inspect.isawaitable(result):
                        result = await result
                except Exception as exc:
                    # hook 异常会被记录，但不会打断运行时 hook 链；
                    # 只有显式 blocking_error 才能阻止工具执行。
                    metadata.setdefault("hook_errors", []).append(str(exc))
                    continue
                if result is None:
                    continue
                metadata.update(result.metadata)
                if result.updated_input is not None:
                    base_input = payload.get("tool_input", {})
                    merged_input = dict(
                        base_input if isinstance(base_input, dict) else {}
                    )
                    merged_input.update(result.updated_input)
                    # 后续 hook 会看到合并后的 tool_input；executor 会在执行
                    # handler 前重新校验最终输入。
                    payload["tool_input"] = merged_input
                if result.blocking_error is not None:
                    blocking = True
                    span.end(
                        {
                            **trace_attributes,
                            "blocking": True,
                            "updated_input": merged_input is not None,
                            "hook_error_count": len(metadata.get("hook_errors", [])),
                        }
                    )
                    return HookResult(
                        blocking_error=result.blocking_error,
                        updated_input=merged_input,
                        metadata=metadata,
                    )
            span.end(
                {
                    **trace_attributes,
                    "blocking": blocking,
                    "updated_input": merged_input is not None,
                    "hook_error_count": len(metadata.get("hook_errors", [])),
                }
            )
            return HookResult(updated_input=merged_input, metadata=metadata)


def _payload_tool_name(payload: HookPayload) -> str | None:
    descriptor = payload.get("descriptor")
    name = getattr(descriptor, "name", None)
    if isinstance(name, str):
        return name
    tool_call = payload.get("tool_call")
    name = getattr(tool_call, "name", None)
    return name if isinstance(name, str) else None


def _payload_tool_call_id(payload: HookPayload) -> str | None:
    tool_call = payload.get("tool_call")
    call_id = getattr(tool_call, "id", None)
    return call_id if isinstance(call_id, str) else None
