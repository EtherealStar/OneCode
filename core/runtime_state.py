"""Mutable state for a single agent runtime session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid

from core.transitions import TransitionReason
from services.model.types import ModelUsage


@dataclass
class RuntimeState:
    usage: ModelUsage = field(default_factory=ModelUsage)
    turn_count: int = 0
    max_turns: int = 20
    has_attempted_reactive_compact: bool = False
    has_escalated_max_output_tokens: bool = False
    max_output_recovery_count: int = 0
    last_transition: TransitionReason | None = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_usage(self, usage: ModelUsage) -> None:
        self.usage.add(usage)

    def set_transition(self, transition: TransitionReason) -> None:
        self.last_transition = transition

    def start_new_session(self) -> str:
        """开启新的运行时会话。

        用于未来 `/clear` 这类清空当前对话的入口。该方法会生成新的
        session UUID，并重置和当前消息链相关的运行时计数、恢复状态与
        metadata；`max_turns` 代表运行时配置，因此不会被重置。
        """

        self.session_id = str(uuid.uuid4())
        self.usage = ModelUsage()
        self.turn_count = 0
        self.has_attempted_reactive_compact = False
        self.has_escalated_max_output_tokens = False
        self.max_output_recovery_count = 0
        self.last_transition = None
        self.metadata.clear()
        return self.session_id
