"""Ordered hook callback registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from services.hooks.events import HookEvent

HookPayload = dict[str, Any]


@dataclass(frozen=True)
class HookResult:
    blocking_error: str | None = None
    updated_input: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


HookCallback = Callable[[HookPayload], HookResult | None]


class HookRegistry:
    def __init__(self) -> None:
        self._callbacks: dict[HookEvent, list[HookCallback]] = {
            event: [] for event in HookEvent
        }

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._callbacks[event].append(callback)

    def run(self, event: HookEvent, payload: HookPayload) -> HookResult:
        merged_input: dict[str, Any] | None = None
        metadata: dict[str, Any] = {}
        for callback in self._callbacks[event]:
            try:
                result = callback(payload)
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
                merged_input = dict(base_input if isinstance(base_input, dict) else {})
                merged_input.update(result.updated_input)
                # 后续 hook 会看到合并后的 tool_input；executor 会在执行
                # handler 前重新校验最终输入。
                payload["tool_input"] = merged_input
            if result.blocking_error is not None:
                return HookResult(
                    blocking_error=result.blocking_error,
                    updated_input=merged_input,
                    metadata=metadata,
                )
        return HookResult(updated_input=merged_input, metadata=metadata)
