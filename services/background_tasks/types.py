"""Shared background task data types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

BackgroundTaskType = Literal["local_bash", "local_agent", "dream"]
BackgroundTaskStatus = Literal["pending", "running", "completed", "failed", "killed"]
TERMINAL_STATUSES: frozenset[BackgroundTaskStatus] = frozenset(
    {"completed", "failed", "killed"}
)


@dataclass(frozen=True)
class BackgroundTaskState:
    id: str
    type: BackgroundTaskType
    status: BackgroundTaskStatus
    description: str
    output_file: str
    start_time: float
    end_time: float | None = None
    tool_use_id: str | None = None
    notified: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_updates(self, **updates: Any) -> "BackgroundTaskState":
        metadata = updates.pop("metadata", None)
        if metadata is not None:
            updates["metadata"] = {**self.metadata, **metadata}
        return replace(self, **updates)
