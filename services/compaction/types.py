"""Shared types for context compaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CompactionTrigger(StrEnum):
    MICRO = "micro"
    AUTO_SESSION_MEMORY = "auto_session_memory"
    AUTO_FULL = "auto_full"
    MANUAL = "manual"
    REACTIVE = "reactive"


@dataclass(frozen=True)
class CompactionConfig:
    default_context_window_tokens: int = 128_000
    summary_output_reserved_tokens: int = 20_000
    auto_compact_buffer_tokens: int = 15_000
    tool_result_budget_chars: int = 200_000
    tool_result_preview_chars: int = 4_000
    microcompact_keep_recent: int = 5
    snip_max_messages: int = 80
    session_memory_min_tokens: int = 10_000
    session_memory_max_tokens: int = 40_000
    session_memory_min_text_messages: int = 5
    max_consecutive_auto_compact_failures: int = 3
    max_reactive_compact_retries: int = 1

    @property
    def effective_context_window_tokens(self) -> int:
        return max(0, self.default_context_window_tokens - self.summary_output_reserved_tokens)

    @property
    def auto_compact_threshold_tokens(self) -> int:
        return max(0, self.effective_context_window_tokens - self.auto_compact_buffer_tokens)


@dataclass(frozen=True)
class CompactBoundary:
    boundary_id: str
    trigger: CompactionTrigger
    token_before: int
    token_after: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompactionResult:
    trigger: CompactionTrigger
    messages: tuple[dict[str, Any], ...]
    token_before: int
    token_after: int
    transcript_refs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
