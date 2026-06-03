"""Mutable state for a single agent runtime session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.transitions import TransitionReason
from services.model.types import ModelUsage


@dataclass
class RuntimeState:
    usage: ModelUsage = field(default_factory=ModelUsage)
    turn_count: int = 0
    max_turns: int = 20
    has_attempted_reactive_compact: bool = False
    max_output_recovery_count: int = 0
    last_transition: TransitionReason | None = None
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_usage(self, usage: ModelUsage) -> None:
        self.usage.add(usage)

    def set_transition(self, transition: TransitionReason) -> None:
        self.last_transition = transition
