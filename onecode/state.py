from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_window: int = 200_000
    occupied_ratio: float = 0.0

    @classmethod
    def from_usage(
        cls,
        usage: dict | None,
        *,
        context_window: int,
        reserved_output_tokens: int,
    ) -> "UsageSnapshot":
        if not usage:
            return cls(context_window=context_window)
        input_tokens = int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("cache_read_input_tokens", 0)
            or 0
        )
        output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        usable_window = max(1, context_window - reserved_output_tokens)
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            context_window=context_window,
            occupied_ratio=input_tokens / usable_window,
        )


@dataclass
class AgentState:
    messages: list[dict] = field(default_factory=list)
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    turn_count: int = 0
    has_reactive_compacted: bool = False
    max_output_recovery_count: int = 0
    last_transition: str | None = None
    consecutive_compact_failures: int = 0
