from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from .model_client import ToolCall

HookEvent = Literal[
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "ToolError",
    "PreCompact",
    "PostCompact",
    "Stop",
]


@dataclass
class HookResult:
    blocked: bool = False
    message: str | None = None
    updated_input: dict | None = None
    force_continue: bool = False


HookCallback = Callable[..., HookResult | None]


class HookRegistry:
    def __init__(self):
        self._callbacks: dict[str, list[HookCallback]] = defaultdict(list)

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        self._callbacks[event].append(callback)

    def emit(self, event: HookEvent, **kwargs) -> HookResult:
        merged = HookResult()
        for callback in self._callbacks.get(event, []):
            result = callback(**kwargs)
            if result is None:
                continue
            if result.updated_input is not None:
                merged.updated_input = result.updated_input
            if result.message:
                merged.message = result.message
            if result.force_continue:
                merged.force_continue = True
            if result.blocked:
                merged.blocked = True
                return merged
        return merged


def register_default_hooks(hooks: HookRegistry, *, verbose: bool = True) -> None:
    dangerous_fragments = (
        "rm -rf /",
        "sudo ",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        "> /dev/sda",
    )

    def dangerous_bash_hook(tool_call: ToolCall, **_) -> HookResult | None:
        if tool_call.name != "bash":
            return None
        command = str(tool_call.input.get("command", ""))
        for fragment in dangerous_fragments:
            if fragment in command:
                return HookResult(blocked=True, message=f"Permission denied: {fragment}")
        return None

    def tool_log_hook(tool_call: ToolCall, **_) -> HookResult | None:
        if verbose:
            preview = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in tool_call.input.items())
            print(f"[tool] {tool_call.name}({preview})")
        return None

    def compact_log_hook(reason: str, **_) -> HookResult | None:
        if verbose:
            print(f"[compact] {reason}")
        return None

    hooks.register("PreToolUse", dangerous_bash_hook)
    hooks.register("PreToolUse", tool_log_hook)
    hooks.register("PreCompact", compact_log_hook)
