"""Background task id generation."""

from __future__ import annotations

import secrets

from services.background_tasks.types import BackgroundTaskType

_PREFIXES: dict[BackgroundTaskType, str] = {
    "local_bash": "b_",
    "local_agent": "a_",
    "dream": "d_",
}


def generate_background_task_id(task_type: BackgroundTaskType) -> str:
    """Return a short random id with a stable type prefix."""

    return f"{_PREFIXES[task_type]}{secrets.token_hex(4)}"
